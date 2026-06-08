export const serverOrigin = process.env.REACT_APP_API_URL || '/api'

export function http(method, url, body, content = 'application/json') {
  if (!url.includes('http')) {
    url = serverOrigin + url
  }

  const options = {
    method,
    credentials: 'include',
    headers: {
      'Content-Type': content
    },
  }

  if (body) {
    options.body = body
  }

  return window.fetch(url, options).then(async res => {
    const payload = await res.json().catch(() => ({
      success: false,
      error: 'Server returned an invalid response.'
    }))

    if (!res.ok && payload.success !== false) {
      return { success: false, error: `Request failed with status ${res.status}`, status: res.status }
    }

    if (!res.ok) {
      payload.status = res.status
    }
    return payload
  }).catch(error => ({
    success: false,
    error: error.message || 'Network request failed.'
  }))
}

export function get(url) {
  return http('GET', url, null)
}

export function post(url, json) {
  return http('POST', url, JSON.stringify(json))

}


export function put(url, json) {
  return http('PUT', url, JSON.stringify(json))
}
