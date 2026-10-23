import asyncio
import pytest
from app.port.exceptions import ConflictException
from app.schemas.renshe_project import RensheProjectWrite, RensheCommitmentTemplateBinding
from app.services.renshe_graduated import save_template
from app.services.renshe_project import RensheProjectService
from tests.integration.db.test_renshe_review_revision import pg_env

pytestmark = [pytest.mark.integration_db, pytest.mark.asyncio]


async def test_two_admins_cannot_overwrite_the_same_product_template(pg_env):
    service = RensheProjectService()
    product = await service.save(RensheProjectWrite(chinese_name='模板并发测试', selection_id='information_security:4:学历型'), admin_id=1)
    first = save_template('第一份.pdf', b'%PDF-1.4\nfirst')
    second = save_template('第二份.pdf', b'%PDF-1.4\nsecond')
    results = await asyncio.gather(*[service.save_commitment_template(product.id,
        RensheCommitmentTemplateBinding(expected_version=product.version, commitment_template=info), admin_id=1) for info in (first, second)], return_exceptions=True)
    succeeded = [item for item in results if not isinstance(item, Exception)]
    assert len(succeeded) == 1
    assert sum(isinstance(item, ConflictException) for item in results) == 1
    current = next(item for item in await service.list_projects() if item.id == product.id)
    assert current.version == product.version + 1
    assert current.commitment_template['sha256'] == succeeded[0].commitment_template['sha256']
