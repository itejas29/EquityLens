import pytest
from datetime import date, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import text
from app.models.pipeline_run import PipelineRun
from app.models.price_history import PriceHistory
from app.models.stock import Stock

def test_single_failure_detected(db_session):
    # Setup
    stock1 = Stock(symbol="RELIANCE", is_active=True)
    stock2 = Stock(symbol="MISSING", is_active=True)
    db_session.add_all([stock1, stock2])
    db_session.commit()
    
    # Mock
    with patch("app.services.incremental._download_incremental_batch") as mock_batch:
        import pandas as pd
        # Return data for RELIANCE but none for MISSING
        df = pd.DataFrame({
            "Date": ["2026-08-15"], "Open": [100], "High": [105], "Low": [95], "Close": [102], "Volume": [1000]
        })
        df.set_index("Date", inplace=True)
        mock_batch.return_value = {"RELIANCE": df}
        
        # Test
        from app.services.incremental import incremental_price_update
        result = incremental_price_update(db_session, today=date(2026, 8, 15))
        
        assert result.requested == 2
        assert result.succeeded == 1
        assert result.failed == 1
        assert result.status == "incomplete"
        
        failed = [r for r in result.results if r.status.value != "SUCCESS"]
        assert len(failed) == 1
        assert failed[0].symbol == "MISSING"
