from fastapi.testclient import TestClient

from tracedeck.config import Config
from tracedeck.web.app import create_app


def test_dashboard_is_local_static_and_has_no_external_assets(tmp_path):
    with TestClient(create_app(Config(data_dir=tmp_path))) as client:
        response = client.get("/", headers={"host": "127.0.0.1"})
        assert response.status_code == 200
        body = response.text
        assert "TraceDeck" in body
        assert "https://" not in body
        assert "/app.js" in body and "/styles.css" in body
        assert "<details" in body and "lifecycle-chart" in body
        script = client.get("/app.js", headers={"host": "127.0.0.1"}).text
        assert "renderCharts" in script and "Hosted tools may be missing" in script
        assert "STALE / INCOMPLETE" in script and "stale_in_progress" in script
        assert "state-panel" in body
        assert "Collector unavailable" in script
        assert 'getJson("/api/refresh", {method: "POST"})' in script
        assert "Last successful refresh" in body
        assert "hasLoadedData" in script
        assert "qualityRecord" in script
        assert "Stale / incomplete" in script
        assert "does not claim the user goal succeeded" in script
        assert 'role="tablist"' in body
        assert 'aria-describedby="token-chart-summary"' in body
        assert "Known token totals by day" in script
        assert "heavy-turn-link" in script
        assert "compactTokens(model.tokens)" in script
        assert "reloadAnalyticsForRange" in script
        assert "No known token usage in the selected range." in script
        assert 'data-view-panel="setup"' in body
        assert '<body data-view="analytics">' in body
        assert 'data-focus="filters"' not in body
        assert "renderSetup" in script
        assert "document.addEventListener(\"keydown\"" in script
        assert "clearFilterState" in script
        assert "dataset.focus === \"filters\"" in script
        assert 'dataset.priority' in script
        css = client.get("/styles.css", headers={"host": "127.0.0.1"}).text
        assert "position:sticky" in css
        assert "overflow-x:visible" in css
        assert ':has(.table-state)' in css
        assert 'data-label="Duration"' in script
