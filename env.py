from pathlib import Path

def parse_env(env_path: Path) -> dict:
    """parse the `env_path` file and loads it a dict"""

    def is_valid_line(line: str) -> bool:
        return bool(line and not line.startswith("#") and len(line.split("=", 1)) == 2)

    def get_key_value(line: str) -> tuple[str, str]:
        key, value = map(lambda ln: ln.strip(), line.split("=", 1))
        value = value.split("#", 1).pop(0).strip()

        return (key, value)

    with env_path.open("r") as file:
        return dict(filter(all, map(get_key_value, filter(is_valid_line, file))))


def load_env() -> dict:
    """loads the .env file and returns the vars"""
    current = Path(__file__).resolve().parent

    while True:
        env = current / ".env"
        if env.is_file():
            return parse_env(env)

        if current == current.parent:
            raise FileExistsError("No .env file found reached root!")

        current = current.parent
