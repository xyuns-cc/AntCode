/**
 * Monaco Editor 配置工具
 * 提供语言识别、主题配置等功能
 */

import type * as Monaco from 'monaco-editor'

/**
 * 根据文件扩展名获取 Monaco 语言 ID
 */
export const getMonacoLanguage = (filename: string): string => {
  const ext = filename.toLowerCase().split('.').pop() || ''
  
  const languageMap: Record<string, string> = {
    // JavaScript/TypeScript
    js: 'javascript',
    jsx: 'javascript',
    ts: 'typescript',
    tsx: 'typescript',
    
    // Python
    py: 'python',
    pyw: 'python',
    
    // Java
    java: 'java',
    
    // C/C++
    c: 'c',
    cpp: 'cpp',
    cc: 'cpp',
    cxx: 'cpp',
    h: 'c',
    hpp: 'cpp',
    
    // C#
    cs: 'csharp',
    
    // Go
    go: 'go',
    
    // Rust
    rs: 'rust',
    
    // PHP
    php: 'php',
    
    // Ruby
    rb: 'ruby',
    
    // Shell
    sh: 'shell',
    bash: 'shell',
    zsh: 'shell',
    
    // HTML/XML
    html: 'html',
    htm: 'html',
    xml: 'xml',
    
    // CSS
    css: 'css',
    scss: 'scss',
    sass: 'sass',
    less: 'less',
    
    // JSON
    json: 'json',
    jsonc: 'json',
    
    // YAML
    yaml: 'yaml',
    yml: 'yaml',
    
    // Markdown
    md: 'markdown',
    markdown: 'markdown',
    
    // SQL
    sql: 'sql',
    
    // Docker
    dockerfile: 'dockerfile',
    
    // Makefile
    makefile: 'makefile',
    mk: 'makefile',
    
    // Protobuf
    proto: 'protobuf',
    
    // GraphQL
    graphql: 'graphql',
    gql: 'graphql',
    
    // Kotlin
    kt: 'kotlin',
    kts: 'kotlin',
    
    // Swift
    swift: 'swift',
    
    // R
    r: 'r',
    
    // Lua
    lua: 'lua',
    
    // Perl
    pl: 'perl',
    pm: 'perl',
    
    // Scala
    scala: 'scala',
    
    // Clojure
    clj: 'clojure',
    cljs: 'clojure',
    
    // Elixir
    ex: 'elixir',
    exs: 'elixir',
    
    // Haskell
    hs: 'haskell',
    
    // Erlang
    erl: 'erlang',
    
    // Dart
    dart: 'dart',
    
    // Vue
    vue: 'html',
    
    // PowerShell
    ps1: 'powershell',
    
    // Ini/Toml
    ini: 'ini',
    toml: 'ini',
    conf: 'ini',
    cfg: 'ini',
    
    // Plain text
    txt: 'plaintext',
    text: 'plaintext',
    log: 'plaintext'
  }
  
  return languageMap[ext] || 'plaintext'
}

/**
 * Monaco Editor 编辑器选项配置
 */
export const getEditorOptions = (
  readOnly: boolean = false,
  isDark: boolean = false
): Monaco.editor.IStandaloneEditorConstructionOptions => ({
  // 基础配置
  automaticLayout: true, // 自动布局
  fontSize: 14,
  fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Consolas, 'Courier New', monospace",
  fontLigatures: true, // 连字
  lineNumbers: 'on',
  readOnly,
  theme: isDark ? 'vs-dark' : 'vs',
  
  // 滚动条
  scrollbar: {
    vertical: 'auto',
    horizontal: 'auto',
    verticalScrollbarSize: 12,
    horizontalScrollbarSize: 12,
    useShadows: false
  },
  
  // 缩进
  tabSize: 2,
  insertSpaces: true,
  detectIndentation: true,
  
  // 编辑器行为
  wordWrap: 'on', // 自动换行
  wrappingIndent: 'indent',
  mouseWheelZoom: true, // Ctrl + 鼠标滚轮缩放
  contextmenu: true, // 右键菜单
  
  // 代码折叠
  folding: true,
  foldingStrategy: 'indentation',
  showFoldingControls: 'mouseover',
  
  // 括号匹配
  matchBrackets: 'always',
  bracketPairColorization: {
    enabled: true
  },
  
  // 代码提示
  quickSuggestions: {
    other: true,
    comments: false,
    strings: false
  },
  suggestOnTriggerCharacters: true,
  acceptSuggestionOnCommitCharacter: true,
  acceptSuggestionOnEnter: 'on',
  
  // 代码补全
  wordBasedSuggestions: 'matchingDocuments',
  
  // 代码片段
  snippetSuggestions: 'top',
  
  // 参数提示
  parameterHints: {
    enabled: true
  },
  
  // 代码格式化
  formatOnPaste: true,
  formatOnType: true,
  
  // 小地图
  minimap: {
    enabled: true,
    maxColumn: 120,
    renderCharacters: false,
    showSlider: 'mouseover'
  },
  
  // 渲染空白字符
  renderWhitespace: 'selection',
  renderControlCharacters: false,
  
  // 光标
  cursorBlinking: 'smooth',
  cursorSmoothCaretAnimation: 'on',
  
  // 选择
  selectionHighlight: true,
  occurrencesHighlight: 'multiFile',
  
  // 查找
  find: {
    addExtraSpaceOnTop: false,
    autoFindInSelection: 'never',
    seedSearchStringFromSelection: 'always'
  },
  
  // 代码高亮
  semanticHighlighting: {
    enabled: true
  },
  
  // 粘性滚动（显示当前作用域）
  stickyScroll: {
    enabled: true,
    maxLineCount: 5
  },
  
  // 悬停提示
  hover: {
    enabled: true,
    delay: 300,
    sticky: true
  },
  
  // 代码镜头
  codeLens: true,
  
  // 内联提示
  inlineSuggest: {
    enabled: true
  },
  
  // 颜色装饰器
  colorDecorators: true,
  
  // 代码操作灯泡
  lightbulb: {
    enabled: 'on'
  },
  
  // 链接检测
  links: true,
  
  // 平滑滚动
  smoothScrolling: true,
  
  // 固定溢出小部件
  fixedOverflowWidgets: true,
  
  // 填充
  padding: {
    top: 16,
    bottom: 16
  },
  
  // 滚动超出最后一行
  scrollBeyondLastLine: false,
  
  // 概览标尺
  overviewRulerLanes: 3,
  
  // 快速建议延迟
  quickSuggestionsDelay: 10
})

/**
 * 获取 Monaco 主题名称
 */
export const getMonacoTheme = (isDark: boolean): string => {
  return isDark ? 'vs-dark' : 'vs'
}

/**
 * 配置 Monaco Editor 全局设置
 */
export const configureMonaco = (monaco: typeof Monaco) => {
  // 配置 JSON 语言
  monaco.languages.json.jsonDefaults.setDiagnosticsOptions({
    validate: true,
    schemas: [],
    allowComments: true,
    trailingCommas: 'ignore'
  })
  
  // 配置 TypeScript/JavaScript 语言
  monaco.languages.typescript.typescriptDefaults.setCompilerOptions({
    target: monaco.languages.typescript.ScriptTarget.ES2020,
    allowNonTsExtensions: true,
    moduleResolution: monaco.languages.typescript.ModuleResolutionKind.NodeJs,
    module: monaco.languages.typescript.ModuleKind.CommonJS,
    noEmit: true,
    esModuleInterop: true,
    jsx: monaco.languages.typescript.JsxEmit.React,
    reactNamespace: 'React',
    allowJs: true,
    typeRoots: ['node_modules/@types']
  })
  
  monaco.languages.typescript.javascriptDefaults.setCompilerOptions({
    target: monaco.languages.typescript.ScriptTarget.ES2020,
    allowNonTsExtensions: true,
    moduleResolution: monaco.languages.typescript.ModuleResolutionKind.NodeJs,
    module: monaco.languages.typescript.ModuleKind.CommonJS,
    noEmit: true,
    esModuleInterop: true,
    jsx: monaco.languages.typescript.JsxEmit.React,
    reactNamespace: 'React',
    allowJs: true
  })
  
  // 配置诊断选项
  monaco.languages.typescript.typescriptDefaults.setDiagnosticsOptions({
    noSemanticValidation: false,
    noSyntaxValidation: false
  })
  
  monaco.languages.typescript.javascriptDefaults.setDiagnosticsOptions({
    noSemanticValidation: false,
    noSyntaxValidation: false
  })
}
