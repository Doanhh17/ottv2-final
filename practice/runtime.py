"""Adapt the ordinary OTTv2 stdin loop to asynchronous browser messages.

Used inside a dedicated Pyodide worker, never by the official match worker.
"""
import ast
import asyncio
import inspect

_ott_inbox = asyncio.Queue()


def _ott_feed(line):
    _ott_inbox.put_nowait(line)


async def _ott_readline():
    return await _ott_inbox.get()


async def _ott_input(prompt=''):
    line = await _ott_readline()
    if not line:
        raise EOFError()
    return line.rstrip('\n')


class __OttLines:
    def __aiter__(self):
        return self

    async def __anext__(self):
        line = await _ott_readline()
        if not line:
            raise StopAsyncIteration()
        return line


def prepare(source):
    tree = ast.parse(source, filename='main.py')
    streams = {'stdin'}
    systems = {'sys'}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            systems.update(alias.asname or alias.name for alias in node.names if alias.name == 'sys')
        if isinstance(node, ast.ImportFrom) and node.module == 'sys':
            streams.update(alias.asname or alias.name for alias in node.names if alias.name == 'stdin')

    def stdin(node):
        return (isinstance(node, ast.Name) and node.id in streams or
                isinstance(node, ast.Attribute) and node.attr == 'stdin'
                and isinstance(node.value, ast.Name) and node.value.id in systems)

    class Input(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            name = None
            if isinstance(node.func, ast.Name) and node.func.id == 'input':
                name = '_ott_input'
            if isinstance(node.func, ast.Attribute) and node.func.attr == 'readline' and stdin(node.func.value):
                name = '_ott_readline'
            if name:
                return ast.copy_location(ast.Await(ast.Call(ast.Name(name, ast.Load()), node.args, node.keywords)), node)
            return node

        def visit_For(self, node):
            self.generic_visit(node)
            if stdin(node.iter):
                return ast.copy_location(ast.AsyncFor(node.target, ast.Name('_ott_lines', ast.Load()),
                    node.body, node.orelse, node.type_comment), node)
            return node

    tree = Input().visit(tree)
    functions = [node for node in ast.walk(tree) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
    asynchronous = {node.name for node in functions if isinstance(node, ast.AsyncFunctionDef)
                    or any(isinstance(child, (ast.Await, ast.AsyncFor)) for child in ast.walk(node))}
    while True:
        before = set(asynchronous)
        for node in functions:
            if any(isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
                   and child.func.id in asynchronous for child in ast.walk(node)):
                asynchronous.add(node.name)
        if before == asynchronous:
            break

    class AsyncCalls(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            self.generic_visit(node)
            if node.name in asynchronous:
                return ast.copy_location(ast.AsyncFunctionDef(**{field: getattr(node, field) for field in node._fields}), node)
            return node

        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id in asynchronous:
                return ast.copy_location(ast.Await(node), node)
            return node

        def visit_Await(self, node):
            node.value = self.generic_visit(node.value)
            return node

    tree = ast.fix_missing_locations(AsyncCalls().visit(tree))
    return compile(tree, 'main.py', 'exec', flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)


async def run_bot(source):
    scope = {'__name__': '__main__', '_ott_readline': _ott_readline,
             '_ott_input': _ott_input, '_ott_lines': __OttLines()}
    pending = eval(prepare(source), scope)
    if inspect.isawaitable(pending):
        await pending
