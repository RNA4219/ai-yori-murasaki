"""旧P0コマンドの移行境界。OracleとCLIの機能試験はtest_v1_oracle/cliへ移行。"""
import json
import pytest
from ai_yori_murasaki.cli import main

@pytest.mark.parametrize('command',['fixtures','genesis','append','blind-view','artifact-put'])
def test_p0_command_requires_explicit_v1_migration(command,capsys):
    assert main([command])==2
    result=json.loads(capsys.readouterr().out)
    assert result['reason_code']=='usage_error'
