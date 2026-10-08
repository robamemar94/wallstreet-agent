from app.application.services.portfolio_service import PortfolioService

tax = PortfolioService._spanish_tax


def test_tramos_base_del_ahorro():
    assert tax(0) == 0 and tax(-100) == 0
    assert round(tax(6_000), 2) == 1_140.00                          # 6.000 × 19 %
    assert round(tax(50_000), 2) == 1_140 + 44_000 * 0.21            # tramo del 21 % hasta 50.000 (no 44.000)
    assert round(tax(200_000), 2) == round(1_140 + 9_240 + 150_000 * 0.23, 2)
    assert round(tax(400_000), 2) == round(1_140 + 9_240 + 34_500 + 100_000 * 0.27 + 100_000 * 0.30, 2)
