from datetime import datetime, timezone

from app.api.system import server_time


class _ClockDb:
    def __init__(self, value):
        self.value = value

    async def scalar(self, statement):
        return self.value


async def test_system_time_returns_database_clock_in_utc():
    now = datetime(2026, 11, 15, 8, 30, 1, 250000, tzinfo=timezone.utc)

    response = await server_time(db=_ClockDb(now))

    assert response.code == 0
    assert response.data.unix == int(now.timestamp())
    assert response.data.unix_ms == int(now.timestamp() * 1000)
    assert response.data.iso == now
    assert response.data.timezone == "UTC"
