/**
 * 验证工具函数
 */

/**
 * 检查是否为有效路径（基本检查）
 */
export function isValidPath(path: string | null | undefined): boolean {
  if (!path || typeof path !== 'string') return false
  // 检查空路径
  if (path.trim() === '') return false
  // 检查常见无效字符（Windows）
  const invalidChars = /[<>"|?*]/
  return !invalidChars.test(path)
}

/**
 * 检查是否为有效文件名
 */
export function isValidFilename(filename: string | null | undefined): boolean {
  if (!filename || typeof filename !== 'string') return false
  if (filename.trim() === '') return false
  // 检查无效字符
  // eslint-disable-next-line no-control-regex -- 这里就是要拦住控制字符（Windows 非法文件名）
  const invalidChars = /[<>:"/\\|?*\x00-\x1f]/
  if (invalidChars.test(filename)) return false
  // 检查保留名称（Windows）
  const reserved = /^(con|prn|aux|nul|com[0-9]|lpt[0-9])$/i
  const baseName = filename.split('.')[0] ?? ''
  return !reserved.test(baseName)
}

/**
 * 检查是否为有效 URL
 */
export function isValidUrl(url: string | null | undefined): boolean {
  if (!url || typeof url !== 'string') return false
  try {
    new URL(url)
    return true
  } catch {
    return false
  }
}

/**
 * 检查是否为有效 IP 地址
 */
export function isValidIp(ip: string | null | undefined): boolean {
  if (!ip || typeof ip !== 'string') return false
  const ipv4 = /^(\d{1,3}\.){3}\d{1,3}$/
  const ipv6 = /^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}$/
  if (ipv4.test(ip)) {
    return ip.split('.').every((part) => {
      const num = parseInt(part, 10)
      return num >= 0 && num <= 255
    })
  }
  return ipv6.test(ip)
}

/**
 * 检查是否为有效端口号
 */
export function isValidPort(port: number | string | null | undefined): boolean {
  if (port === null || port === undefined) return false
  const num = typeof port === 'string' ? parseInt(port, 10) : port
  return Number.isInteger(num) && num >= 1 && num <= 65535
}

/**
 * 检查是否为有效 Cron 表达式（基本检查）
 */
export function isValidCron(cron: string | null | undefined): boolean {
  if (!cron || typeof cron !== 'string') return false
  const parts = cron.trim().split(/\s+/)
  // 标准 cron 有 5 个部分，部分系统支持 6 个（含秒）
  return parts.length >= 5 && parts.length <= 6
}

/**
 * 检查是否为空值
 */
export function isEmpty(value: unknown): boolean {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return value.trim() === ''
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return false
}

/**
 * 检查是否为有效的视频文件扩展名
 */
export function isVideoExtension(ext: string | null | undefined): boolean {
  if (!ext) return false
  const videoExts = [
    'mp4',
    'mkv',
    'avi',
    'mov',
    'wmv',
    'flv',
    'webm',
    'm4v',
    'ts',
    'rmvb',
    'rm',
    '3gp',
    'mpg',
    'mpeg',
    'vob',
    'iso',
  ]
  return videoExts.includes(ext.toLowerCase().replace('.', ''))
}

/**
 * 检查是否为有效的图片文件扩展名
 */
export function isImageExtension(ext: string | null | undefined): boolean {
  if (!ext) return false
  const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'bmp', 'svg', 'ico']
  return imageExts.includes(ext.toLowerCase().replace('.', ''))
}
