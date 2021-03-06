__copyright__ = "Copyright (c) 2020 Jina AI Limited. All rights reserved."
__license__ = "Apache-2.0"

from typing import Iterator, Union, Tuple, Sequence

from ... import Request
from ...enums import RequestType, DataInputType
from ...excepts import BadDocType
from ...helper import batch_iterator
from ...types.document import Document, DocumentSourceType, DocumentContentType
from ...types.querylang import QueryLang
from ...types.sets.querylang_set import AcceptQueryLangType

GeneratorSourceType = Iterator[Union[DocumentContentType,
                                     DocumentSourceType,
                                     Tuple[DocumentContentType, DocumentContentType],
                                     Tuple[DocumentSourceType, DocumentSourceType]]]


def _build_doc(data, data_type: DataInputType, override_doc_id, **kwargs) -> Tuple['Document', 'DataInputType']:
    def _build_doc_from_content():
        with Document(**kwargs) as d:
            d.content = data
        # note that there is no point to check override_doc_id here
        # as no doc_id is given when use _generate in this way
        return d, DataInputType.CONTENT

    if data_type == DataInputType.AUTO or data_type == DataInputType.DOCUMENT:
        if isinstance(data, Document):
            # if incoming is already primitive type Document, then all good, best practice!
            return data, DataInputType.DOCUMENT
        try:
            d = Document(data, **kwargs)
            if override_doc_id:
                d.update_id()
            return d, DataInputType.DOCUMENT
        except BadDocType:
            # AUTO has a fallback, now reconsider it as content
            if data_type == DataInputType.AUTO:
                return _build_doc_from_content()
            else:
                raise
    elif data_type == DataInputType.CONTENT:
        return _build_doc_from_content()


def _generate(data: GeneratorSourceType,
              batch_size: int = 0,
              mode: RequestType = RequestType.INDEX,
              mime_type: str = None,
              override_doc_id: bool = True,
              queryset: Union[AcceptQueryLangType, Iterator[AcceptQueryLangType]] = None,
              data_type: DataInputType = DataInputType.AUTO,
              **kwargs  # do not remove this, add on purpose to suppress unknown kwargs
              ) -> Iterator['Request']:
    """
    :param data_type: if ``data`` is an iterator over self-contained document, i.e. :class:`DocumentSourceType`;
            or an interator over possible Document content (set to text, blob and buffer).
    :return:
    """

    _kwargs = dict(mime_type=mime_type, length=batch_size, weight=1.0)

    for batch in batch_iterator(data, batch_size):
        req = Request()
        req.request_type = str(mode)
        for content in batch:
            if isinstance(content, tuple) and len(content) == 2:
                # content comes in pair,  will take the first as the input and the second as the groundtruth

                # note how data_type is cached
                d, data_type = _build_doc(content[0], data_type, override_doc_id, **_kwargs)
                gt, _ = _build_doc(content[1], data_type, override_doc_id, **_kwargs)
                req.docs.append(d)
                req.groundtruths.append(gt)
            else:
                d, data_type = _build_doc(content, data_type, override_doc_id, **_kwargs)
                req.docs.append(d)

        if isinstance(queryset, Sequence):
            req.queryset.extend(queryset)
        elif queryset is not None:
            req.queryset.append(queryset)

        yield req


def index(*args, **kwargs):
    """Generate a indexing request"""
    yield from _generate(*args, **kwargs)


def train(*args, **kwargs):
    """Generate a training request """
    yield from _generate(*args, **kwargs)
    req = Request()
    req.train.flush = True
    yield req


def search(*args, **kwargs):
    """Generate a searching request """
    if ('top_k' in kwargs) and (kwargs['top_k'] is not None):
        from jina.drivers.search import VectorSearchDriver
        topk_ql = QueryLang(VectorSearchDriver(top_k=kwargs['top_k'], priority=1))
        if 'queryset' not in kwargs:
            kwargs['queryset'] = [topk_ql]
        else:
            kwargs['queryset'].append(topk_ql)
    yield from _generate(*args, **kwargs)


def evaluate(*args, **kwargs):
    """Generate an evaluation request """
    yield from _generate(*args, **kwargs)
