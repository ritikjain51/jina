__copyright__ = "Copyright (c) 2020 Jina AI Limited. All rights reserved."
__license__ = "Apache-2.0"

from collections import defaultdict
from typing import Dict, List, Callable, Union

from . import BaseExecutor, AnyExecutor


class CompoundExecutor(BaseExecutor):
    """A :class:`CompoundExecutor` is a set of multiple executors.
    The most common usage is chaining a pipeline of executors, where the
    input of the current is the output of the former.

    A common use case of :class:`CompoundExecutor` is to glue multiple :class:`BaseExecutor` together, instead of breaking them into different Pods.

    **Example 1: a compound Chunk Indexer that does vector indexing and key-value index**

    .. highlight:: yaml
    .. code-block:: yaml

        !CompoundExecutor
        components:
          - !NumpyIndexer
            with:
              index_filename: vec.gz
            metas:
              name: vecidx_exec  # a customized name
              workspace: $TEST_WORKDIR
          - !BinaryPbIndexer
            with:
              index_filename: chunk.gz
            metas:
              name: chunkidx_exec
              workspace: $TEST_WORKDIR
        metas:
          name: chunk_compound_indexer
          workspace: $TEST_WORKDIR
        requests:
          on:
            SearchRequest:
              - !VectorSearchDriver
                with:
                  executor: vecidx_exec
            IndexRequest:
              - !VectorIndexDriver
                with:
                  executor: vecidx_exec
            ControlRequest:
              - !ControlReqDriver {}

    **Example 2: a compound crafter that first craft the doc and then segment **

    .. highlight:: yaml
    .. code-block:: yaml

        !CompoundExecutor
        components:
          - !GifNameRawSplit
            metas:
              name: name_split  # a customized name
              workspace: $TEST_WORKDIR
          - !GifPreprocessor
            with:
              every_k_frame: 2
              from_buffer: true
            metas:
              name: gif2chunk_preprocessor  # a customized name
        metas:
          name: compound_crafter
          workspace: $TEST_WORKDIR
          py_modules: gif2chunk.py
        requests:
          on:
            IndexRequest:
              - !DocCraftDriver
                with:
                  executor: name_split
              - !SegmentDriver
                with:
                  executor: gif2chunk_preprocessor
            ControlRequest:
              - !ControlReqDriver {}


    One can access the component of a :class:`CompoundExecutor` via index, e.g.

    .. highlight:: python
    .. code-block:: python

        c = BaseExecutor.load_config('compound-example.yaml')
        assertTrue(c[0] == c['dummyA-1ef90ea8'])
        c[0].add(obj)

    .. note::
        All components ``workspace`` and ``pea_workspace`` are overrided by their :class:`CompoundExecutor` counterparts.

    .. warning::

        When sub-component is external, ``py_modules`` must be given at root level ``metas`` not at the sub-level.

    """

    class _FnWrapper:
        def __init__(self, fns):
            self.fns = fns

        def __call__(self, *args, **kwargs):
            r = []
            for f in self.fns:
                r.append(f())
            return r

    class _FnAllWrapper(_FnWrapper):
        def __call__(self, *args, **kwargs):
            return all(super().__call__(*args, **kwargs))

    class _FnOrWrapper(_FnWrapper):
        def __call__(self, *args, **kwargs):
            return any(super().__call__(*args, **kwargs))

    def __init__(self, routes: Dict[str, Dict] = None, resolve_all: bool = True, *args, **kwargs):
        """ Create a new :class:`CompoundExecutor` object

        :param routes: a map of function routes. The key is the function name, the value is a tuple of two pieces,
            where the first element is the name of the referred component (``metas.name``) and the second element
            is the name of the referred function.

            .. seealso::

                :func:`add_route`
        :param resolve_all: universally add ``*_all()`` to all functions that have the identical name

        Example:

        We have two dummy executors as follows:

        .. highlight:: python
        .. code-block:: python

            class dummyA(BaseExecutor):
                def say(self):
                    return 'a'

                def sayA(self):
                    print('A: im A')


            class dummyB(BaseExecutor):
                def say(self):
                    return 'b'

                def sayB(self):
                    print('B: im B')

        and we create a :class:`CompoundExecutor` consisting of these two via

        .. highlight:: python
        .. code-block:: python

            da, db = dummyA(), dummyB()
            ce = CompoundExecutor()
            ce.components = lambda: [da, db]

        Now the new executor ``ce`` have two new methods, i.e :func:`ce.sayA` and :func:`ce.sayB`. They point to the original
        :func:`dummyA.sayA` and :func:`dummyB.sayB` respectively. One can say ``ce`` has inherited these two methods.

        The interesting part is :func:`say`, as this function name is shared between :class:`dummyA` and :class:`dummyB`.
        It requires some resolution. When `resolve_all=True`, then a new function :func:`say_all` is add to ``ce``.
        ``ce.say_all`` works as if you call :func:`dummyA.sayA` and :func:`dummyB.sayB` in a row. This
        makes sense in some cases such as training, saving. In other cases, it may require a more sophisticated resolution,
        where one can use :func:`add_route` to achieve that. For example,

        .. highlight:: python
        .. code-block:: python

            ce.add_route('say', db.name, 'say')
            assert b.say() == 'b'

        Such resolution is what we call **routes** here, and it can be specified in advance with the
        arguments ``routes`` in :func:`__init__`, or using YAML.

        .. highlight:: yaml
        .. code-block:: yaml

            !CompoundExecutor
            components: ...
            with:
              resolve_all: true
              routes:
                say:
                - dummyB-e3acc910
                - say

        """
        super().__init__(*args, **kwargs)
        self._components = None  # type: List[AnyExecutor]
        self._routes = routes
        self._is_updated = False  #: the internal update state of this compound executor
        self.resolve_all = resolve_all

    @property
    def is_trained(self) -> bool:
        """Return ``True`` only if all components are trained (i.e. ``is_trained=True``)"""
        return self.components and all(c.is_trained for c in self.components)

    @property
    def is_updated(self) -> bool:
        """Return ``True``  if any components is updated"""
        return (self.components and any(c.is_updated for c in self.components)) or self._is_updated

    @is_updated.setter
    def is_updated(self, val: bool) -> None:
        """Set :attr:`is_updated` for this :class:`CompoundExecutor`. Note, not to all its components """
        self._is_updated = val

    @is_trained.setter
    def is_trained(self, val: bool) -> None:
        """Set :attr:`is_trained` for all components of this :class:`CompoundExecutor` """
        for c in self.components:
            c.is_trained = val

    def save(self, filename: str = None) -> bool:
        """
        Serialize this compound executor along with all components in it to binary files

        :param filename: file path of the serialized file, if not given then :attr:`save_abspath` is used
        :return: successfully dumped or not

        It uses ``pickle`` for dumping.
        """

        for c in self.components:
            c.save()
        super().save(filename=filename)  # do i really need to save the compound executor itself
        return True

    @property
    def components(self) -> List[AnyExecutor]:
        """Return all component executors as a list. The list follows the order as defined in the YAML config or the
        pre-given order when calling the setter. """
        return self._components

    @components.setter
    def components(self, comps: Callable[[], List]) -> None:
        """Set the components of this executors

        :param comps: a function returns a list of executors
        """
        if not callable(comps):
            raise TypeError('components must be a callable function that returns '
                            'a List[BaseExecutor]')
        if not getattr(self, 'init_from_yaml', False):
            self._components = comps()
            if not isinstance(self._components, list):
                raise TypeError(f'components expect a list of executors, receiving {type(self._components)!r}')
            # self._set_comp_workspace()
            self._set_routes()
            self._resolve_routes()
        else:
            self.logger.debug('components is omitted from construction, as it is initialized from yaml config')

    def _set_comp_workspace(self) -> None:
        # overrider the workspace setting for all components
        for c in self.components:
            c.separated_workspace = self.separated_workspace
            c.workspace = self.workspace
            c.pea_workspace = self.current_workspace

    def _resolve_routes(self) -> None:
        if self._routes:
            for f, v in self._routes.items():
                for kk, vv in v.items():
                    self.add_route(f, kk, vv)

    def add_route(self, fn_name: str, comp_name: str, comp_fn_name: str, is_stored: bool = False) -> None:
        """Create a new function for this executor which refers to the component's function

        This will create a new function :func:`fn_name` which actually refers to ``components[comp_name].comp_fn_name``.
        It is useful when two components have a function with duplicated name and one wants to resolve this duplication.

        :param fn_name: the name of the new function
        :param comp_name: the name of the referred component, defined in ``metas.name``
        :param comp_fn_name: the name of the referred function of ``comp_name``
        :param is_stored: if ``True`` then this change will be stored in the config and affects future :func:`save` and
            :func:`save_config`

        """
        for c in self.components:
            if c.name == comp_name and hasattr(c, comp_fn_name) and callable(getattr(c, comp_fn_name)):
                setattr(self, fn_name, getattr(c, comp_fn_name))
                if is_stored:
                    if not self._routes:
                        self._routes = {}
                    self._routes[fn_name] = {comp_name: comp_fn_name}
                    self.is_updated = True
                return
        else:
            raise AttributeError(f'bad names: {comp_name} and {comp_fn_name}')

    def _set_routes(self) -> None:
        # add all existing routes
        r = defaultdict(list)

        for c in self.components:
            for method in BaseExecutor.exec_methods:
                if hasattr(c, method):
                    r[method].append((c.name, getattr(c, method)))

        new_routes = []
        bad_routes = []
        for k, v in r.items():
            if len(v) == 1:
                setattr(self, k, v[0][1])
            elif len(v) > 1:
                if self.resolve_all:
                    new_r = f'{k}_all'
                    fns = self._FnWrapper([vv[1] for vv in v])
                    setattr(self, new_r, fns)
                    self.logger.debug(f'function "{k}" appears multiple times in {v}')
                    self.logger.debug(f'a new function "{new_r}" is added to {self!r} by iterating over all')
                    new_routes.append(new_r)
                else:
                    self.logger.warning(
                        f'function "{k}" appears multiple times in {v}, it needs to be resolved manually before using.')
                    bad_routes.append(k)
        if new_routes:
            self.logger.debug(f'new functions added: {new_routes!r}')
        if bad_routes:
            self.logger.warning(f'unresolvable functions: {bad_routes!r}')

    def close(self) -> None:
        """Close all components and release the resources"""
        if self.components:
            for c in self.components:
                c.close()
        super().close()

    @classmethod
    def to_yaml(cls, representer, data):
        tmp = super()._dump_instance_to_yaml(data)
        tmp['components'] = data.components
        return representer.represent_mapping('!' + cls.__name__, tmp)

    @classmethod
    def from_yaml(cls, constructor, node):
        obj, data, from_dump = super()._get_instance_from_yaml(constructor, node)
        if not from_dump and 'components' in data:
            obj.components = lambda: data['components']
        return obj

    def __contains__(self, item: str):
        if isinstance(item, str):
            for c in self.components:
                if c.name == item:
                    return True
            return False
        else:
            raise TypeError('CompoundExecutor only support string type "in"')

    def __getitem__(self, item: Union[int, str]):
        if isinstance(item, int):
            return self.components[item]
        elif isinstance(item, str):
            for c in self.components:
                if c.name == item:
                    return c
        else:
            raise TypeError('CompoundExecutor only supports int or string index')

    def __iter__(self):
        return self.components.__iter__()
