import type React from 'react'
import { useMemo, useState } from 'react'
import { AutoComplete, Input } from 'antd'
import { GlobalOutlined } from '@ant-design/icons'
import type { DefaultOptionType } from 'antd/es/select'

// 时区数据，按地区分类
const TIMEZONE_DATA: Record<string, Array<{ value: string; label: string }>> = {
  '亚洲': [
    { value: 'Asia/Shanghai', label: 'Asia/Shanghai     中国/上海' },
    { value: 'Asia/Hong_Kong', label: 'Asia/Hong_Kong     中国/香港' },
    { value: 'Asia/Taipei', label: 'Asia/Taipei     中国/台北' },
    { value: 'Asia/Macau', label: 'Asia/Macau     中国/澳门' },
    { value: 'Asia/Chongqing', label: 'Asia/Chongqing     中国/重庆' },
    { value: 'Asia/Urumqi', label: 'Asia/Urumqi     中国/乌鲁木齐' },
    { value: 'Asia/Tokyo', label: 'Asia/Tokyo     日本/东京' },
    { value: 'Asia/Seoul', label: 'Asia/Seoul     韩国/首尔' },
    { value: 'Asia/Singapore', label: 'Asia/Singapore     新加坡' },
    { value: 'Asia/Kuala_Lumpur', label: 'Asia/Kuala_Lumpur     马来西亚/吉隆坡' },
    { value: 'Asia/Bangkok', label: 'Asia/Bangkok     泰国/曼谷' },
    { value: 'Asia/Ho_Chi_Minh', label: 'Asia/Ho_Chi_Minh     越南/胡志明市' },
    { value: 'Asia/Jakarta', label: 'Asia/Jakarta     印度尼西亚/雅加达' },
    { value: 'Asia/Manila', label: 'Asia/Manila     菲律宾/马尼拉' },
    { value: 'Asia/Kolkata', label: 'Asia/Kolkata     印度/加尔各答' },
    { value: 'Asia/Mumbai', label: 'Asia/Mumbai     印度/孟买' },
    { value: 'Asia/Dubai', label: 'Asia/Dubai     阿联酋/迪拜' },
    { value: 'Asia/Riyadh', label: 'Asia/Riyadh     沙特阿拉伯/利雅得' },
    { value: 'Asia/Tehran', label: 'Asia/Tehran     伊朗/德黑兰' },
    { value: 'Asia/Jerusalem', label: 'Asia/Jerusalem     以色列/耶路撒冷' },
    { value: 'Asia/Almaty', label: 'Asia/Almaty     哈萨克斯坦/阿拉木图' },
    { value: 'Asia/Dhaka', label: 'Asia/Dhaka     孟加拉国/达卡' },
    { value: 'Asia/Karachi', label: 'Asia/Karachi     巴基斯坦/卡拉奇' },
  ],
  '欧洲': [
    { value: 'Europe/London', label: 'Europe/London     英国/伦敦' },
    { value: 'Europe/Paris', label: 'Europe/Paris     法国/巴黎' },
    { value: 'Europe/Berlin', label: 'Europe/Berlin     德国/柏林' },
    { value: 'Europe/Rome', label: 'Europe/Rome     意大利/罗马' },
    { value: 'Europe/Madrid', label: 'Europe/Madrid     西班牙/马德里' },
    { value: 'Europe/Amsterdam', label: 'Europe/Amsterdam     荷兰/阿姆斯特丹' },
    { value: 'Europe/Brussels', label: 'Europe/Brussels     比利时/布鲁塞尔' },
    { value: 'Europe/Vienna', label: 'Europe/Vienna     奥地利/维也纳' },
    { value: 'Europe/Zurich', label: 'Europe/Zurich     瑞士/苏黎世' },
    { value: 'Europe/Stockholm', label: 'Europe/Stockholm     瑞典/斯德哥尔摩' },
    { value: 'Europe/Oslo', label: 'Europe/Oslo     挪威/奥斯陆' },
    { value: 'Europe/Copenhagen', label: 'Europe/Copenhagen     丹麦/哥本哈根' },
    { value: 'Europe/Helsinki', label: 'Europe/Helsinki     芬兰/赫尔辛基' },
    { value: 'Europe/Warsaw', label: 'Europe/Warsaw     波兰/华沙' },
    { value: 'Europe/Prague', label: 'Europe/Prague     捷克/布拉格' },
    { value: 'Europe/Athens', label: 'Europe/Athens     希腊/雅典' },
    { value: 'Europe/Moscow', label: 'Europe/Moscow     俄罗斯/莫斯科' },
    { value: 'Europe/Istanbul', label: 'Europe/Istanbul     土耳其/伊斯坦布尔' },
    { value: 'Europe/Dublin', label: 'Europe/Dublin     爱尔兰/都柏林' },
    { value: 'Europe/Lisbon', label: 'Europe/Lisbon     葡萄牙/里斯本' },
  ],
  '美洲': [
    { value: 'America/New_York', label: 'America/New_York     美国/纽约' },
    { value: 'America/Los_Angeles', label: 'America/Los_Angeles     美国/洛杉矶' },
    { value: 'America/Chicago', label: 'America/Chicago     美国/芝加哥' },
    { value: 'America/Denver', label: 'America/Denver     美国/丹佛' },
    { value: 'America/Phoenix', label: 'America/Phoenix     美国/凤凰城' },
    { value: 'America/Anchorage', label: 'America/Anchorage     美国/安克雷奇' },
    { value: 'America/Toronto', label: 'America/Toronto     加拿大/多伦多' },
    { value: 'America/Vancouver', label: 'America/Vancouver     加拿大/温哥华' },
    { value: 'America/Montreal', label: 'America/Montreal     加拿大/蒙特利尔' },
    { value: 'America/Mexico_City', label: 'America/Mexico_City     墨西哥/墨西哥城' },
    { value: 'America/Sao_Paulo', label: 'America/Sao_Paulo     巴西/圣保罗' },
    { value: 'America/Buenos_Aires', label: 'America/Buenos_Aires     阿根廷/布宜诺斯艾利斯' },
    { value: 'America/Lima', label: 'America/Lima     秘鲁/利马' },
    { value: 'America/Bogota', label: 'America/Bogota     哥伦比亚/波哥大' },
    { value: 'America/Santiago', label: 'America/Santiago     智利/圣地亚哥' },
    { value: 'America/Caracas', label: 'America/Caracas     委内瑞拉/加拉加斯' },
  ],
  '大洋洲': [
    { value: 'Australia/Sydney', label: 'Australia/Sydney     澳大利亚/悉尼' },
    { value: 'Australia/Melbourne', label: 'Australia/Melbourne     澳大利亚/墨尔本' },
    { value: 'Australia/Brisbane', label: 'Australia/Brisbane     澳大利亚/布里斯班' },
    { value: 'Australia/Perth', label: 'Australia/Perth     澳大利亚/珀斯' },
    { value: 'Australia/Adelaide', label: 'Australia/Adelaide     澳大利亚/阿德莱德' },
    { value: 'Pacific/Auckland', label: 'Pacific/Auckland     新西兰/奥克兰' },
    { value: 'Pacific/Fiji', label: 'Pacific/Fiji     斐济' },
    { value: 'Pacific/Honolulu', label: 'Pacific/Honolulu     美国/夏威夷' },
    { value: 'Pacific/Guam', label: 'Pacific/Guam     关岛' },
  ],
  '非洲': [
    { value: 'Africa/Cairo', label: 'Africa/Cairo     埃及/开罗' },
    { value: 'Africa/Johannesburg', label: 'Africa/Johannesburg     南非/约翰内斯堡' },
    { value: 'Africa/Lagos', label: 'Africa/Lagos     尼日利亚/拉各斯' },
    { value: 'Africa/Nairobi', label: 'Africa/Nairobi     肯尼亚/内罗毕' },
    { value: 'Africa/Casablanca', label: 'Africa/Casablanca     摩洛哥/卡萨布兰卡' },
    { value: 'Africa/Algiers', label: 'Africa/Algiers     阿尔及利亚/阿尔及尔' },
    { value: 'Africa/Tunis', label: 'Africa/Tunis     突尼斯' },
    { value: 'Africa/Accra', label: 'Africa/Accra     加纳/阿克拉' },
  ],
  '通用时区': [
    { value: 'UTC', label: 'UTC     协调世界时' },
    { value: 'GMT', label: 'GMT     格林威治标准时间' },
    { value: 'Etc/GMT+0', label: 'Etc/GMT+0     GMT+0' },
    { value: 'Etc/GMT-8', label: 'Etc/GMT-8     GMT+8（东八区）' },
    { value: 'Etc/GMT-9', label: 'Etc/GMT-9     GMT+9（东九区）' },
    { value: 'Etc/GMT+5', label: 'Etc/GMT+5     GMT-5（西五区）' },
    { value: 'Etc/GMT+8', label: 'Etc/GMT+8     GMT-8（西八区）' },
  ],
}

interface TimezoneSelectProps {
  value?: string
  onChange?: (value: string) => void
  placeholder?: string
  style?: React.CSSProperties
  disabled?: boolean
}

const TimezoneSelect: React.FC<TimezoneSelectProps> = ({
  value,
  onChange,
  placeholder = '请选择或输入时区，如 Asia/Shanghai',
  style,
  disabled = false,
}) => {
  const [searchValue, setSearchValue] = useState('')

  // 构建分组选项
  const options = useMemo(() => {
    const result: DefaultOptionType[] = []
    
    Object.entries(TIMEZONE_DATA).forEach(([region, timezones]) => {
      const filteredTimezones = timezones.filter(
        (tz) =>
          !searchValue ||
          tz.value.toLowerCase().includes(searchValue.toLowerCase()) ||
          tz.label.toLowerCase().includes(searchValue.toLowerCase())
      )
      
      if (filteredTimezones.length > 0) {
        result.push({
          label: (
            <span style={{ fontWeight: 600, color: 'var(--ant-color-primary)' }}>
              <GlobalOutlined style={{ marginRight: 6 }} />
              {region}
            </span>
          ),
          options: filteredTimezones.map((tz) => ({
            value: tz.value,
            label: (
              <span style={{ fontFamily: 'monospace' }}>
                {tz.label}
              </span>
            ),
          })),
        })
      }
    })
    
    return result
  }, [searchValue])

  const handleSearch = (text: string) => {
    setSearchValue(text)
  }

  const handleChange = (newValue: string) => {
    onChange?.(newValue)
    setSearchValue('')
  }

  return (
    <AutoComplete
      value={value}
      onChange={handleChange}
      onSearch={handleSearch}
      options={options}
      style={{ width: '100%', ...style }}
      placeholder={placeholder}
      disabled={disabled}
      allowClear
      filterOption={false}
    >
      <Input placeholder={placeholder} />
    </AutoComplete>
  )
}

export default TimezoneSelect
