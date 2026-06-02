"""
Test refresh token race-condition safety.

Verifies that AuthService.refresh:
- Uses redis_client.getdel (atomic GET + DELETE) instead of get
- Raises UnauthorizedException when the token does not exist (already consumed or expired)
- Issues a new token pair via setex on success
"""
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.exceptions import UnauthorizedException
from app.services.auth import REFRESH_TOKEN_PREFIX, AuthService


class TestRefreshTokenRace:
    """Tests that AuthService.refresh uses GETDEL and handles missing tokens correctly."""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _make_mock_user(user_id: int = 1, openid: str = "wx_test", is_active: bool = True):
        user = MagicMock()
        user.id = user_id
        user.openid = openid
        user.is_active = is_active
        return user

    # ------------------------------------------------------------------
    # getdel is called, not get
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_refresh_uses_getdel_not_get(self):
        """AuthService.refresh MUST call redis_client.getdel, never get."""
        svc = AuthService()
        user = self._make_mock_user()
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value.get = AsyncMock(return_value=user)

        with (
            patch("app.services.auth.redis_client") as mock_redis,
            patch("app.services.auth.get_db_ctx", return_value=mock_db_ctx),
        ):
            mock_redis.getdel = AsyncMock(return_value="1")
            mock_redis.setex = AsyncMock()

            await svc.refresh("valid_token")

            mock_redis.getdel.assert_called_once_with(f"{REFRESH_TOKEN_PREFIX}valid_token")
            mock_redis.get.assert_not_called()

    # ------------------------------------------------------------------
    # missing / consumed token → UnauthorizedException
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_refresh_with_nonexistent_token_raises_unauthorized(self):
        """When getdel returns None the caller MUST receive UnauthorizedException."""
        svc = AuthService()

        with patch("app.services.auth.redis_client") as mock_redis:
            mock_redis.getdel = AsyncMock(return_value=None)

            with pytest.raises(UnauthorizedException) as exc_info:
                await svc.refresh("expired_or_consumed_token")

            assert "无效或已过期" in exc_info.value.message

    # ------------------------------------------------------------------
    # deactivated user during refresh
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_refresh_deactivated_user_raises_unauthorized(self):
        """When the user is inactive or deleted, refresh must raise UnauthorizedException."""
        svc = AuthService()
        inactive_user = self._make_mock_user(is_active=False)
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value.get = AsyncMock(return_value=inactive_user)

        with (
            patch("app.services.auth.redis_client") as mock_redis,
            patch("app.services.auth.get_db_ctx", return_value=mock_db_ctx),
        ):
            mock_redis.getdel = AsyncMock(return_value="1")

            with pytest.raises(UnauthorizedException) as exc_info:
                await svc.refresh("token_for_deactivated")

            assert "不存在或已注销" in exc_info.value.message

    # ------------------------------------------------------------------
    # successful refresh flow
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_refresh_success_returns_new_token_pair(self):
        """A valid, unconsumed token should yield a fresh access+refresh pair."""
        svc = AuthService()
        user = self._make_mock_user(user_id=42, openid="openid_42")
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value.get = AsyncMock(return_value=user)

        with (
            patch("app.services.auth.redis_client") as mock_redis,
            patch("app.services.auth.get_db_ctx", return_value=mock_db_ctx),
        ):
            mock_redis.getdel = AsyncMock(return_value="42")
            mock_redis.setex = AsyncMock()

            response = await svc.refresh("valid_token")

            assert response.access_token
            assert response.refresh_token
            assert response.expires_in > 0
            # Verify new refresh token is persisted
            mock_redis.setex.assert_called_once()
            args, _kwargs = mock_redis.setex.call_args
            assert args[0].startswith(REFRESH_TOKEN_PREFIX)
            assert int(args[2]) == 42

    # ------------------------------------------------------------------
    # concurrent safety: second caller gets nothing
    # ------------------------------------------------------------------
    @pytest.mark.asyncio
    async def test_concurrent_refresh_second_caller_rejected(self):
        """Simulate two concurrent refreshes: the second must fail because
        getdel atomically removes the token on first read."""
        svc = AuthService()
        user = self._make_mock_user()
        mock_db_ctx = AsyncMock()
        mock_db_ctx.__aenter__.return_value.get = AsyncMock(return_value=user)

        call_count = 0

        async def mock_getdel(_key: str):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return "1"  # first caller succeeds
            return None      # second caller finds nothing

        with (
            patch("app.services.auth.redis_client") as mock_redis,
            patch("app.services.auth.get_db_ctx", return_value=mock_db_ctx),
        ):
            mock_redis.getdel = mock_getdel
            mock_redis.setex = AsyncMock()

            # First caller — succeeds
            resp1 = await svc.refresh("shared_token")
            assert resp1.access_token

            # Second caller — rejected
            with pytest.raises(UnauthorizedException):
                await svc.refresh("shared_token")
