"""Package stickers with a shared application fallback for older open packages."""
from zephie_rolling_on.paths import project_root


def read_import_art(package, which: str) -> bytes | None:
    if which not in ('busy', 'done'):
        raise ValueError('Unknown import artwork')
    getter = getattr(package, 'import_art_png', None)
    if callable(getter):
        try:
            data = getter(which)
            if data:
                return data
        except Exception:
            pass
    path = project_root() / 'assets' / 'ui' / f'zehpie_import_{which}.png'
    return path.read_bytes() if path.is_file() else None
