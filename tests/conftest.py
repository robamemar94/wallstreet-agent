"""Los tests nunca tocan la base de datos real: la app arranca contra una copia temporal de data/alpha_flow.db.
(Va aquí, antes de que ningún test importe la app, porque el engine se crea al importar app.infrastructure.db.database.)"""
import os
import shutil
import tempfile

import pytest

_tmp = tempfile.mkdtemp(prefix="alpha_flow_test_")
_db = os.path.join(_tmp, "alpha_flow.db")
if os.path.exists("data/alpha_flow.db"):
    shutil.copy2("data/alpha_flow.db", _db)
os.environ["ALPHA_FLOW_DB"] = _db


def pytest_configure(config):
    config.addinivalue_line("markers", "network: necesita internet (yfinance…); se salta por defecto, `-m network` para pasarlo")


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_tmp, ignore_errors=True)


@pytest.fixture(autouse=True)
def _real_db_untouched():
    """Red de seguridad: si algo vuelve a apuntar a la base de datos real, el test falla."""
    from app.infrastructure.db.database import engine
    assert os.path.abspath(engine.url.database) == os.path.abspath(_db)
