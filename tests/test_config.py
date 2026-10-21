import os
import stat

import pytest

from motor import config

CLAVE = "AIzaFAKE-clave-de-prueba-1234567890abcd"


def test_guardar_leer_y_borrar():
    config.guardar({"gemini_api_key": CLAVE, "gemini_modelo": "modelo-x"})
    assert config.gemini_api_key() == CLAVE
    assert config.leer()["gemini_modelo"] == "modelo-x"
    config.guardar({"gemini_api_key": ""})
    assert config.gemini_api_key() is None
    assert config.leer()["gemini_modelo"] == "modelo-x"


def test_archivo_solo_para_el_usuario():
    config.guardar({"gemini_api_key": CLAVE})
    modo = stat.S_IMODE(os.stat(config.ruta()).st_mode)
    assert modo == 0o600


def test_publico_no_expone_la_clave():
    config.guardar({"gemini_api_key": CLAVE})
    publico = config.publico()
    assert CLAVE not in str(publico)
    assert publico["gemini"] == {"configurada": True, "terminacion": "abcd",
                                 "desde_entorno": False, "modelo": None}


def test_variable_de_entorno_tiene_prioridad(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "x" * 30)
    assert config.gemini_api_key() == "x" * 30
    assert config.publico()["gemini"]["desde_entorno"]


@pytest.mark.parametrize("cambios", [{"otra_cosa": 1}, {"gemini_api_key": "corta"},
                                     {"gemini_api_key": "con espacios " * 3}])
def test_rechaza(cambios):
    with pytest.raises(ValueError):
        config.guardar(cambios)


def test_qwen_url_por_defecto_guardada_y_entorno(monkeypatch):
    from motor import qwen

    assert config.qwen_url() == qwen.URL_POR_DEFECTO
    config.guardar({"qwen_url": " http://192.168.1.9:1234/ "})
    assert config.qwen_url() == "http://192.168.1.9:1234"
    monkeypatch.setenv("QWEN_URL", "http://otro:1")
    assert config.qwen_url() == "http://otro:1" and config.publico()["qwen"]["desde_entorno"]
    monkeypatch.delenv("QWEN_URL")
    config.guardar({"qwen_url": ""})
    assert config.qwen_url() == qwen.URL_POR_DEFECTO


@pytest.mark.parametrize("url", ["localhost:1234", "ftp://x", "http://a b"])
def test_qwen_url_invalida(url):
    with pytest.raises(ValueError, match="http"):
        config.guardar({"qwen_url": url})
