def greet(name: str, exited: bool = False) -> str:
    return f"Hello {name}{"!" if exited else ""}"