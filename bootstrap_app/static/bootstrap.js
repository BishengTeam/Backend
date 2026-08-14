(() => {
  'use strict'

  let token = ''
  let pollTimer = null

  const byId = (id) => document.getElementById(id)
  const sessionCard = byId('session-card')
  const statusCard = byId('status-card')
  const configuration = byId('configuration')
  const administrator = byId('administrator')
  const message = byId('message')

  function setMessage(text, isError = false) {
    message.textContent = text || ''
    message.classList.toggle('error', Boolean(isError))
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
      const error = new Error(payload.message || `请求失败 (${response.status})`)
      error.status = response.status
      error.payload = payload
      throw error
    }
    return payload
  }

  function renderStatus(state) {
    byId('phase').textContent = state.phase
    byId('installation-id').textContent = state.installation_id
    statusCard.classList.remove('hidden')
    sessionCard.classList.add('hidden')
    configuration.classList.toggle('hidden', state.phase !== 'NEW')
    administrator.classList.toggle('hidden', state.phase !== 'AWAITING_ADMIN')
    const failure = byId('failure')
    const retry = byId('retry')
    if (state.last_failure) {
      failure.textContent = `${state.last_failure.stage}: ${state.last_failure.code}`
      failure.classList.remove('hidden')
      retry.classList.remove('hidden')
    } else {
      failure.textContent = ''
      failure.classList.add('hidden')
      retry.classList.add('hidden')
    }
    if (state.phase !== 'NEW' && state.phase !== 'CONFIGURED') {
      setMessage('宿主机正在执行部署阶段，可安全关闭页面后重新连接。')
    }
  }

  async function refreshStatus() {
    if (!token) return
    try {
      const state = await api('/api/bootstrap/status')
      renderStatus(state)
    } catch (error) {
      if (error.status === 410) {
        clearInterval(pollTimer)
        setMessage('初始化入口已永久关闭。请转到 Admin 查看部署验收状态。')
        configuration.classList.add('hidden')
        administrator.classList.add('hidden')
        return
      }
      if (error.status === 401) {
        clearInterval(pollTimer)
        sessionCard.classList.remove('hidden')
        statusCard.classList.add('hidden')
        setMessage('一次性 Token 无效。', true)
        return
      }
      setMessage(error.message, true)
    }
  }

  async function fileText(form, name) {
    const input = form.elements[name]
    if (!input.files || input.files.length !== 1) {
      throw new Error(`请选择文件：${name}`)
    }
    if (input.files[0].size > 64 * 1024) {
      throw new Error(`文件过大：${name}`)
    }
    return input.files[0].text()
  }

  byId('connect').addEventListener('click', async () => {
    token = byId('token').value.trim()
    byId('token').value = ''
    if (!token) {
      setMessage('请输入一次性 Token。', true)
      return
    }
    await refreshStatus()
    if (token && !pollTimer) pollTimer = setInterval(refreshStatus, 3000)
  })

  configuration.addEventListener('submit', async (event) => {
    event.preventDefault()
    const form = event.currentTarget
    const submit = form.querySelector('button[type="submit"]')
    submit.disabled = true
    setMessage('正在验证并原子写入配置……')
    try {
      const data = Object.fromEntries(new FormData(form).entries())
      delete data.wechat_pay_private_key_file
      delete data.wechat_pay_public_key_file
      delete data.recovery_public_key_file
      data.postgres_port = Number(data.postgres_port)
      for (const optional of ['postgres_password', 'redis_url']) {
        if (!data[optional]) data[optional] = null
      }
      data.wechat_pay_private_key_pem = await fileText(form, 'wechat_pay_private_key_file')
      data.wechat_pay_public_key_pem = await fileText(form, 'wechat_pay_public_key_file')
      data.recovery_public_key_pem = await fileText(form, 'recovery_public_key_file')
      const state = await api('/api/bootstrap/configure', {
        method: 'POST',
        body: JSON.stringify(data)
      })
      form.reset()
      renderStatus(state)
      setMessage('配置已安全提交，宿主机将继续质量门禁和部署。')
    } catch (error) {
      setMessage(error.message, true)
    } finally {
      submit.disabled = false
    }
  })

  byId('retry').addEventListener('click', async () => {
    try {
      const state = await api('/api/bootstrap/retry', { method: 'POST' })
      renderStatus(state)
      setMessage('已清除当前失败标记，宿主机将重试当前步骤。')
    } catch (error) {
      setMessage(error.message, true)
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
    submit.disabled = true
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
      submit.disabled = false
    }
  })

  const fragment = new URLSearchParams(window.location.hash.replace(/^#/, ''))
  const fragmentToken = fragment.get('token')
  if (fragmentToken) {
    token = fragmentToken
    history.replaceState(null, '', `${location.pathname}${location.search}`)
    refreshStatus()
    pollTimer = setInterval(refreshStatus, 3000)
  }
})()
