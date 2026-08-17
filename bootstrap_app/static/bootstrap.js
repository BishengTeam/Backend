(() => {
  'use strict'

  let token = ''
  let pollTimer = null
  let lastPhase = ''

  const byId = (id) => document.getElementById(id)
  const sessionCard = byId('session-card')
  const statusCard = byId('status-card')
  const configuration = byId('configuration')
  const administrator = byId('administrator')
  const message = byId('message')

  const phaseMeta = {
    NEW: ['等待部署配置', '填写并验证新服务器的正式环境配置', 1],
    CONFIGURED: ['配置已锁定', '配置已安全写入，即将开始质量门禁', 2],
    QUALITY_RUNNING: ['质量门禁运行中', '正在检查 Backend、Admin、迁移和发布产物', 2],
    QUALITY_PASSED: ['质量门禁通过', '正在准备数据库与 Redis 基础设施', 3],
    INFRA_READY: ['基础设施已就绪', '正在执行正式数据库迁移', 3],
    MIGRATED: ['数据库迁移完成', '正在切换到首位管理员创建步骤', 4],
    AWAITING_ADMIN: ['等待创建管理员', '请创建首位超级管理员以继续部署', 4],
    ADMIN_CREATED: ['管理员已创建', '正在写入正式种子数据', 4],
    SEEDED: ['正式数据已就绪', '正在生成加密恢复包', 5],
    RECOVERY_VERIFIED: ['恢复包已验证', '正在启动 Backend、Worker 与 Admin', 5],
    INSTALLED_PENDING_UAT: ['安装完成，等待 UAT', '服务已启动，请在 Admin 完成真实环境验收', 6],
    PRODUCTION_ACCEPTED: ['生产验收完成', '本次初始化已完成并永久封存', 6]
  }

  const optionalGroups = [
    {
      element: document.querySelector('[data-optional-group="oss"]'),
      label: '阿里云 OSS',
      names: ['oss_endpoint', 'oss_bucket', 'oss_access_key_id', 'oss_access_key_secret']
    }
  ]

  function setMessage(text, isError = false) {
    message.textContent = text || ''
    message.classList.toggle('error', Boolean(isError))
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer)
    pollTimer = null
  }

  function startPolling() {
    if (token && !pollTimer) pollTimer = setInterval(refreshStatus, 3000)
  }

  async function api(path, options = {}) {
    const response = await fetch(path, {
      ...options,
      cache: 'no-store',
      headers: {
        Authorization: `Bearer ${token}`,
        ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        ...(options.headers || {})
      }
    })
    let payload = null
    try {
      payload = await response.json()
    } catch (_) {
      payload = { message: '初始化服务返回了无效响应' }
    }
    if (!response.ok) {
      const detail = typeof payload.detail === 'string' ? payload.detail : ''
      const error = new Error(payload.message || detail || `请求失败 (${response.status})`)
      error.status = response.status
      error.payload = payload
      throw error
    }
    return payload
  }

  function updateProgress(phase) {
    const meta = phaseMeta[phase] || [phase, '正在读取部署阶段', 1]
    const activeStep = meta[2]
    for (const item of byId('progress-list').querySelectorAll('li')) {
      const step = Number(item.dataset.step)
      item.classList.toggle('complete', step < activeStep || activeStep > 5)
      item.classList.toggle('active', step === activeStep && activeStep <= 5)
    }
    byId('phase').textContent = meta[0]
    byId('phase').title = phase
    byId('phase-description').textContent = meta[1]
  }

  function renderStatus(state) {
    updateProgress(state.phase)
    byId('installation-id').textContent = state.installation_id
    statusCard.classList.remove('hidden')
    sessionCard.classList.add('hidden')
    configuration.classList.toggle('hidden', state.phase !== 'NEW')
    administrator.classList.toggle('hidden', state.phase !== 'AWAITING_ADMIN')

    const failure = byId('failure')
    const failurePanel = byId('failure-panel')
    const retry = byId('retry')
    if (state.last_failure) {
      failure.textContent = `${state.last_failure.stage}: ${state.last_failure.code}`
      failurePanel.classList.remove('hidden')
      retry.classList.remove('hidden')
    } else {
      failure.textContent = ''
      failurePanel.classList.add('hidden')
      retry.classList.add('hidden')
    }

    if (state.phase !== lastPhase) {
      if (state.phase !== 'NEW' && state.phase !== 'AWAITING_ADMIN' && !state.last_failure) {
        setMessage('宿主机正在执行部署任务。现在可以安全关闭页面，稍后使用同一 Token 重新连接。')
      }
      lastPhase = state.phase
    }
  }

  async function refreshStatus() {
    if (!token) return
    try {
      const state = await api('/api/bootstrap/status')
      renderStatus(state)
    } catch (error) {
      if (error.status === 410) {
        stopPolling()
        token = ''
        setMessage('初始化入口已永久关闭。请转到 Admin 查看部署验收状态。')
        configuration.classList.add('hidden')
        administrator.classList.add('hidden')
        return
      }
      if (error.status === 401) {
        stopPolling()
        token = ''
        sessionCard.classList.remove('hidden')
        statusCard.classList.add('hidden')
        setMessage('一次性 Token 无效，请重新复制终端中的完整 Token。', true)
        return
      }
      setMessage(error.message, true)
    }
  }

  async function fileText(form, name, label) {
    const input = form.elements[name]
    if (!input.files || input.files.length !== 1) {
      throw new Error(`请选择${label}`)
    }
    if (input.files[0].size > 64 * 1024) {
      throw new Error(`${label}超过 64 KiB`)
    }
    return input.files[0].text()
  }

  function optionalValues(group) {
    return group.names.map((name) => configuration.elements[name].value.trim())
  }

  function renderOptionalGroup(group) {
    const values = optionalValues(group)
    const count = values.filter(Boolean).length
    const configured = count === values.length
    const partial = count > 0 && !configured
    group.element.dataset.configured = String(configured)
    group.element.dataset.partial = String(partial)
    const badge = group.element.querySelector('.optional-badge')
    badge.textContent = configured
      ? '已完整配置'
      : partial
        ? `待补全 · ${count}/${values.length}`
        : '可选 · 未配置'
  }

  function normalizeOptionalGroup(data, group) {
    const values = optionalValues(group)
    const count = values.filter(Boolean).length
    if (count > 0 && count < values.length) {
      const missing = group.names
        .filter((_, index) => !values[index])
        .map((name) => configuration.elements[name].closest('.field').querySelector('.field-label').textContent.trim())
      throw new Error(`${group.label}请完整填写或全部留空；还缺少：${missing.join('、')}`)
    }
    group.names.forEach((name, index) => {
      data[name] = values[index] || null
    })
  }

  function syncInfrastructureMode({ initial = false } = {}) {
    const external = configuration.elements.deployment_mode.value === 'external'
    const host = configuration.elements.postgres_host
    const port = configuration.elements.postgres_port
    if (!initial) {
      if (external && host.value.trim() === 'db') host.value = ''
      if (!external) {
        host.value = 'db'
        port.value = '5432'
      }
    }
    for (const wrapper of configuration.querySelectorAll('[data-external-field]')) {
      const input = wrapper.querySelector('input')
      input.disabled = !external
      input.required = external
      wrapper.classList.toggle('is-disabled', !external)
    }
    byId('deployment-mode-help').textContent = external
      ? '连接已有的数据服务；提交前会验证目标为空且可访问'
      : '适合新服务器，自动创建并管理数据容器'
  }

  function setButtonBusy(button, busy, busyText = '正在处理…') {
    if (!button.dataset.label) button.dataset.label = button.innerHTML
    button.disabled = busy
    button.innerHTML = busy ? busyText : button.dataset.label
  }

  async function connect() {
    token = byId('token').value.trim()
    byId('token').value = ''
    if (!token) {
      setMessage('请输入一次性 Token。', true)
      return
    }
    setMessage('正在建立安全初始化会话…')
    await refreshStatus()
    startPolling()
  }

  byId('connect').addEventListener('click', connect)
  byId('token').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault()
      connect()
    }
  })

  configuration.elements.deployment_mode.addEventListener('change', () => syncInfrastructureMode())
  for (const group of optionalGroups) {
    for (const name of group.names) {
      configuration.elements[name].addEventListener('input', () => renderOptionalGroup(group))
    }
    renderOptionalGroup(group)
  }
  syncInfrastructureMode({ initial: true })

  configuration.addEventListener('submit', async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    const submit = form.querySelector('button[type="submit"]')
    setButtonBusy(submit, true, '正在验证配置…')
    setMessage('正在执行离线校验与外部服务探针，请勿重复提交。')
    try {
      const data = Object.fromEntries(new FormData(form).entries())
      delete data.wechat_pay_private_key_file
      delete data.wechat_pay_public_key_file
      delete data.recovery_public_key_file
      data.postgres_port = Number(data.postgres_port)
      for (const optional of ['postgres_password', 'redis_url']) {
        if (!data[optional]) data[optional] = null
      }
      for (const group of optionalGroups) normalizeOptionalGroup(data, group)
      data.wechat_pay_private_key_pem = await fileText(form, 'wechat_pay_private_key_file', '商户私钥 PEM 文件')
      data.wechat_pay_public_key_pem = await fileText(form, 'wechat_pay_public_key_file', '微信支付公钥 PEM 文件')
      data.recovery_public_key_pem = await fileText(form, 'recovery_public_key_file', '恢复 RSA 公钥 PEM 文件')
      const state = await api('/api/bootstrap/configure', {
        method: 'POST',
        body: JSON.stringify(data)
      })
      form.reset()
      syncInfrastructureMode({ initial: true })
      optionalGroups.forEach(renderOptionalGroup)
      renderStatus(state)
      setMessage('配置已安全提交。宿主机正在继续质量门禁和部署。')
    } catch (error) {
      setMessage(error.message, true)
    } finally {
      setButtonBusy(submit, false)
    }
  })

  byId('retry').addEventListener('click', async () => {
    const retry = byId('retry')
    setButtonBusy(retry, true, '正在提交重试…')
    try {
      const state = await api('/api/bootstrap/retry', { method: 'POST' })
      renderStatus(state)
      setMessage('已清除当前失败标记，宿主机将重试当前步骤。')
    } catch (error) {
      setMessage(error.message, true)
    } finally {
      setButtonBusy(retry, false)
    }
  })

  administrator.addEventListener('submit', async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    const username = form.elements.username.value.trim()
    const password = form.elements.password.value
    const passwordConfirm = form.elements.password_confirm.value
    if (password !== passwordConfirm) {
      setMessage('两次输入的密码不一致。', true)
      return
    }
    const submit = form.querySelector('button[type="submit"]')
    setButtonBusy(submit, true, '正在创建管理员…')
    try {
      const state = await api('/api/bootstrap/admin', {
        method: 'POST',
        body: JSON.stringify({ username, password })
      })
      form.reset()
      renderStatus(state)
      setMessage('超级管理员已创建，宿主机将继续正式种子和恢复包步骤。')
    } catch (error) {
      form.elements.password.value = ''
      form.elements.password_confirm.value = ''
      setMessage(error.message, true)
    } finally {
      setButtonBusy(submit, false)
    }
  })

  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const fragmentToken = fragment.get('token')
  if (fragmentToken) {
    token = fragmentToken
    history.replaceState(null, '', `${location.pathname}${location.search}`)
    refreshStatus().then(startPolling)
  }
})()
