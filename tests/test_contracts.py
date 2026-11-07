"""未検収P0からv1への形式境界。通常のv1契約はtest_v1_contractsで検証する。"""
import pytest
from ai_yori_murasaki.contracts import validate
from ai_yori_murasaki.jsonio import DataError
from tests.helpers import BUNDLE

@pytest.mark.parametrize('kind', list(BUNDLE))
def test_historical_p0_contract_is_not_silently_accepted(kind):
    with pytest.raises(DataError):
        validate(kind, BUNDLE[kind])
