"""Process-wide serialized Agg rendering; import only in rendering code."""
from contextlib import contextmanager
from contextvars import ContextVar
from io import BytesIO
from threading import RLock

import matplotlib

# Configure before pyplot is imported or any figures are created.
matplotlib.use('Agg')
import matplotlib.pyplot as plt


_lock = RLock()
_figures = ContextVar('owned_render_figures', default=None)


@contextmanager
def render_session(style=None):
    """Lock covers rcParams, artist creation, drawing, encoding and cleanup."""
    with _lock:
        owned = []
        token = _figures.set(owned)
        try:
            # Every job starts from defaults; external pyplot styles cannot leak in.
            defaults = {key: value for key, value in matplotlib.rcParamsDefault.items() if key != 'backend'}
            with matplotlib.rc_context({**defaults, 'text.usetex': False,
                                        'text.parse_math': False, **(style or {})}):
                yield
        finally:
            for fig in owned:
                plt.close(fig)
            _figures.reset(token)


def subplots(nrows=1, ncols=1, *, figsize=None, **kwargs):
    owned = _figures.get()
    if owned is None:
        raise RuntimeError('Figure creation requires a serialized render session.')
    fig = plt.figure(figsize=figsize)
    owned.append(fig)
    return fig, fig.subplots(nrows, ncols, **kwargs)


def png(fig):
    if _figures.get() is None or fig not in _figures.get():
        raise RuntimeError('Encoding requires an owned figure in a serialized render session.')
    try:
        with BytesIO() as image:
            fig.savefig(image, format='png', dpi=120, bbox_inches='tight')
            return image.getvalue()
    finally:
        plt.close(fig)
