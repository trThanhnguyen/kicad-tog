from collections.abc import Iterator


def tokenize(text: str) -> Iterator[str]:
    in_quotes = False
    escaped = False
    token = []

    for char in text:
        if in_quotes:
            token.append(char)

            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_quotes = False

        elif char == '"':
            token.append(char)
            in_quotes = True

        elif char in "()":
            if token:
                yield "".join(token)
                token = []
            yield char

        elif char.isspace():
            if token:
                yield "".join(token)
                token = []

        else:
            token.append(char)

    if in_quotes:
        raise ValueError("Unterminated quoted string")

    if token:
        yield "".join(token)



def parse(text: str) -> list:
    stack = []
    root = None

    for token in tokenize(text):

        if token == "(":
            node = []

            if stack:
                stack[-1].append(node)
            elif root is None:
                root = node
            else:
                raise ValueError("Multiple root expressions")

            stack.append(node)

        elif token == ")":
            if not stack:
                raise ValueError("Unexpected closing parenthesis")

            stack.pop()

        else:
            if not stack:
                raise ValueError("Atom outside expression")

            # Remove surrounding quotes and decode escaped quotes/backslashes.
            if token.startswith('"') and token.endswith('"'):
                value = token[1:-1]
                decoded = []
                i = 0

                while i < len(value):
                    if (value[i] == "\\"
                            and i + 1 < len(value)
                            and value[i + 1] in ('"', "\\")):
                        i += 1

                    decoded.append(value[i])
                    i += 1

                token = "".join(decoded)

            stack[-1].append(token)

    if stack:
        raise ValueError("Unclosed parenthesis")

    if root is None:
        raise ValueError("Empty expression")

    return root


def children(node: list, tag: str) -> list[list]:
    return [
        item
        for item in node[1:]
        if isinstance(item, list) and item and item[0] == tag
    ]


def child(node: list, tag: str) -> list | None:
    for item in node[1:]:
        if isinstance(item, list) and item and item[0] == tag:
            return item

    return None
