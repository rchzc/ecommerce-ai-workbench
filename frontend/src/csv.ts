// 浏览器端 CSV 解析。
//
// 只做一件事：把粘贴进来 / 上传的 CSV 文本变成字典数组。
// 支持引号包裹的字段（字段里有逗号或换行时必须），这是 Excel 导出的标准行为。
// 不做类型推断：表格里的 "007" 是字符串不是数字，业务编号的前导零不能丢。

export interface ParsedTable {
  header: string[]
  rows: Record<string, string>[]
}

export function parseCsv(text: string): ParsedTable {
  const clean = text.replace(/^\ufeff/, '').replace(/\r\n/g, '\n').replace(/\r/g, '\n')
  const records = splitRecords(clean).filter((r) => r.some((cell) => cell.trim() !== ''))
  if (records.length < 2) return { header: [], rows: [] }

  const header = dedupe(records[0].map((h) => h.trim()))
  const rows = records.slice(1).map((cells) => {
    const row: Record<string, string> = {}
    header.forEach((key, i) => {
      row[key] = (cells[i] ?? '').trim()
    })
    return row
  })
  return { header, rows }
}

/** 按行切分，同时处理引号内的换行 */
function splitRecords(text: string): string[][] {
  const records: string[][] = []
  let record: string[] = []
  let cell = ''
  let inQuotes = false

  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (inQuotes) {
      if (ch === '"') {
        // 连续两个引号是转义后的一个引号
        if (text[i + 1] === '"') {
          cell += '"'
          i++
        } else {
          inQuotes = false
        }
      } else {
        cell += ch
      }
      continue
    }
    if (ch === '"') inQuotes = true
    else if (ch === ',') {
      record.push(cell)
      cell = ''
    } else if (ch === '\n') {
      record.push(cell)
      records.push(record)
      record = []
      cell = ''
    } else {
      cell += ch
    }
  }
  record.push(cell)
  records.push(record)
  return records
}

function dedupe(header: string[]): string[] {
  const seen = new Map<string, number>()
  return header.map((h, i) => {
    const name = h || `col_${i + 1}`
    const count = seen.get(name) ?? 0
    seen.set(name, count + 1)
    return count === 0 ? name : `${name}_${count}`
  })
}

/** 把字典数组还原成 CSV，用于"改完再导出"和兜底下载 */
export function toCsv(header: string[], rows: Record<string, string>[]): string {
  const esc = (v: string) => (/[",\n]/.test(v) ? `"${v.replace(/"/g, '""')}"` : v)
  const lines = [header.map(esc).join(',')]
  for (const row of rows) {
    lines.push(header.map((h) => esc(row[h] ?? '')).join(','))
  }
  return lines.join('\n')
}
