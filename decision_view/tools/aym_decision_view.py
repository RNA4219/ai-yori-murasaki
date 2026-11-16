"""Compatibility CLI for decision-view JSON. The package is the only implementation."""
from ai_yori_murasaki.decision_view import *  # noqa: F401,F403
from ai_yori_murasaki.decision_view import main

if __name__ == "__main__":
    raise SystemExit(main())
