from dataclasses import dataclass

FIRST = '#include <stdio.h>\n\nint main(void) {\n    printf("0\\n");\n    return 0;\n}\n'
LOOP = '#include <stdio.h>\n\nint main(void) {\n    int n;\n    scanf("%d", &n);\n\n    for (int i = 1; i < n; i++) {\n        printf("%d\\n", i);\n    }\n\n    return 0;\n}\n'

@dataclass(frozen=True)
class Task:
    id: str
    title: str
    source: str
    inputs: tuple[int, ...]

TASKS = {t.id: t for t in [
    Task('tower_light_01', '第一束光', FIRST, (1,)),
    Task('tower_light_02', '为什么最后一盏不亮', LOOP, (3,)),
    Task('tower_light_03', '点亮整座塔', LOOP, (0, 1, 3, 6)),
]}

def expected(n: int) -> str:
    return '\n'.join(str(i) for i in range(1, n + 1))

def matches(stdout: str, n: int) -> bool:
    # Only CRLF and one terminal newline are normalized. Do not strip spaces.
    output = stdout.replace('\r\n', '\n')
    if n == 0:
        return output == ''
    if output.endswith('\n'):
        output = output[:-1]
    return output == expected(n)
