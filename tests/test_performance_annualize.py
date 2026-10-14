"""Rendimiento: no se anualiza con menos de un año y todas las alfas van contra el índice de referencia."""
import pytest

from app.application.services.performance_service import annualized, reference_benchmark


def test_menos_de_un_ano_no_se_anualiza():
    # 8 meses al +28,4 %: se queda en +28,4 % (anualizado saldría un +45 %)
    assert annualized(10.0, 12.84, 0.67) == pytest.approx(28.4)


def test_con_un_ano_o_mas_se_anualiza():
    assert annualized(10.0, 12.1, 2.0) == pytest.approx(10.0)


def test_indice_de_referencia_del_ajuste():
    assert reference_benchmark("SPY") == "S&P 500"
    assert reference_benchmark("urth") == "MSCI World"
    assert reference_benchmark(None) == "S&P 500"
