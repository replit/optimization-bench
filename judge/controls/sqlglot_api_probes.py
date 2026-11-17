"""Protected API exerciser. Only inputs are mounted; host compares its results."""
import json
import logging
from pathlib import Path
import sys
sys.path.insert(0, '/source')
import sqlglot
from sqlglot import exp


def operation(row):
    args = row.get('args', [])
    kwargs = row.get('kwargs', {}).copy()
    for name in ('error_level', 'unsupported_level'):
        if name in kwargs:
            kwargs[name] = getattr(sqlglot.ErrorLevel, kwargs[name])
    api = row['api']
    if api == 'transpile':
        return sqlglot.transpile(*args, **kwargs)
    if api == 'tokenize':
        return [{'type': t.token_type.name, 'text': t.text, 'line': t.line,
                 'col': t.col, 'start': t.start, 'end': t.end, 'comments': t.comments}
                for t in sqlglot.tokenize(*args, **kwargs)]
    if api == 'ast_copy_mutation':
        tree = sqlglot.parse_one(*args, **kwargs)
        original = tree.sql()
        twin = tree.copy()
        twin.find(exp.Identifier).set('this', 'changed_name')
        return {'original': original, 'after_copy_mutation': tree.sql(),
                'copy': twin.sql(), 'fresh_parse': sqlglot.parse_one(*args, **kwargs).sql(),
                'parents': all(c.parent is not None for c in twin.walk() if c is not twin)}
    if api == 'custom_dialect':
        from sqlglot.dialects.dialect import Dialect
        from sqlglot.tokens import Tokenizer as TokenizerBase, TokenType
        from sqlglot.generator import Generator as GeneratorBase
        class Custom(Dialect):
            class Tokenizer(TokenizerBase):
                QUOTES = ["'", '"']
                IDENTIFIERS = ['`']
                KEYWORDS = {**TokenizerBase.KEYWORDS, 'INT64': TokenType.BIGINT}
            class Generator(GeneratorBase):
                TRANSFORMS = {**GeneratorBase.TRANSFORMS, exp.Array:
                              lambda self, e: f"[{self.expressions(e)}]"}
        tree = sqlglot.parse_one(args[0], read=Custom)
        return {'custom': tree.sql(dialect=Custom), 'duckdb': tree.sql(dialect='duckdb')}
    raise ValueError('Unknown protected probe operation')


class Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.WARNING)
        self.messages = []
    def emit(self, record):
        self.messages.append(record.getMessage())


if __name__ == '__main__':
    rows = json.loads(Path('/inputs/probes.json').read_text())
    if isinstance(rows, dict):
        rows = rows['cases']
    results = {}
    logger = logging.getLogger('sqlglot')
    for row in rows:
        handler = Capture()
        logger.addHandler(handler)
        try:
            result = {'status': 'ok', 'output': operation(row)}
        except Exception as error:
            result = {'status': 'error', 'exception': type(error).__module__ + '.' + type(error).__name__,
                      'message': str(error)}
        finally:
            logger.removeHandler(handler)
        result['warnings'] = handler.messages
        results[row['id']] = result
    print(json.dumps({'module': sqlglot.__file__, 'results': results}, ensure_ascii=False))
