"""Реестр коннекторов. Импорт модуля регистрирует площадку автоматически."""
from __future__ import annotations

from .base import (  # noqa: F401
    BaseSource,
    HttpClient,
    RawTender,
    SourceRequiresAuth,
    SourceUnavailable,
    all_sources,
    get_source,
    register,
)

# Порядок импорта = порядок появления площадок в админке.
from . import eis  # noqa: F401,E402
from . import b2b_center  # noqa: F401,E402
from . import bidzaar  # noqa: F401,E402
from . import torgi_gov  # noqa: F401,E402
from . import unreachable  # noqa: F401,E402
