import type React from 'react'
import { useRef, useEffect } from 'react'
import Editor from '@monaco-editor/react'
import type { OnMount, OnChange } from '@monaco-editor/react'
import { useThemeContext } from '@/contexts/ThemeContext'
import type { editor } from 'monaco-editor'
import { APP_BRAND_NAME } from '@/config/app'

export interface CodeEditorProps {
  value?: string
  language?: string
  onChange?: (value: string | undefined) => void
  height?: string | number
  width?: string | number
  readOnly?: boolean
  placeholder?: string
  options?: editor.IStandaloneEditorConstructionOptions
  onMount?: OnMount
}

// 语言映射
const LANGUAGE_MAP: Record<string, string> = {
  python: 'python',
  javascript: 'javascript',
  typescript: 'typescript',
  java: 'java',
  cpp: 'cpp',
  c: 'c',
  go: 'go',
  rust: 'rust',
  html: 'html',
  css: 'css',
  json: 'json',
  xml: 'xml',
  yaml: 'yaml',
  markdown: 'markdown',
  sql: 'sql',
  shell: 'shell',
  bash: 'bash',
  powershell: 'powershell'
}

// 默认代码模板
const DEFAULT_CODE_TEMPLATES: Record<string, string> = {
  python: `#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
"""

def main():
    """主函数"""
    print("Hello, ${APP_BRAND_NAME}!")
    # 在这里编写你的代码逻辑
    pass

if __name__ == "__main__":
    main()
`,
  javascript: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

function main() {
    console.log("Hello, ${APP_BRAND_NAME}!");
    // 在这里编写你的代码逻辑
}

// 执行主函数
main();
`,
  typescript: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

interface Config {
    name: string;
    version: string;
}

function main(): void {
    console.log("Hello, ${APP_BRAND_NAME}!");
    // 在这里编写你的代码逻辑
}

// 执行主函数
main();
`,
  java: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

public class Main {
    public static void main(String[] args) {
        System.out.println("Hello, ${APP_BRAND_NAME}!");
        // 在这里编写你的代码逻辑
    }
}
`,
  go: `package main

import "fmt"

/*
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
*/

func main() {
    fmt.Println("Hello, ${APP_BRAND_NAME}!")
    // 在这里编写你的代码逻辑
}
`,
  rust: `/*
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
*/

fn main() {
    println!("Hello, ${APP_BRAND_NAME}!");
    // 在这里编写你的代码逻辑
}
`,
  cpp: `/*
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
*/

#include <iostream>
using namespace std;

int main() {
    cout << "Hello, ${APP_BRAND_NAME}!" << endl;
    // 在这里编写你的代码逻辑
    return 0;
}
`
}

const CodeEditor: React.FC<CodeEditorProps> = ({
  value,
  language = 'python',
  onChange,
  height = 400,
  width = '100%',
  readOnly = false,
  placeholder,
  options = {},
  onMount
}) => {
  const { isDark } = useThemeContext()
  const editorRef = useRef<editor.IStandaloneCodeEditor | null>(null)

  // 获取Monaco语言标识符
  const getMonacoLanguage = (lang: string): string => {
    return LANGUAGE_MAP[lang] || lang
  }

  // 获取默认代码模板
  const getDefaultTemplate = (lang: string): string => {
    return DEFAULT_CODE_TEMPLATES[lang] || `// ${lang} code
// Hello, ${APP_BRAND_NAME}!`
  }

  // 编辑器挂载时的处理
  const handleEditorDidMount: OnMount = (editor, monaco) => {
    editorRef.current = editor

    // 配置编辑器主题
    monaco.editor.defineTheme('antcode-dark', {
      base: 'vs-dark',
      inherit: true,
      rules: [],
      colors: {
        'editor.background': '#1f1f1f',
        'editor.foreground': '#d4d4d4',
        'editorLineNumber.foreground': '#858585',
        'editorLineNumber.activeForeground': '#c6c6c6',
        'editor.selectionBackground': '#264f78',
        'editor.inactiveSelectionBackground': '#3a3d41'
      }
    })

    monaco.editor.defineTheme('antcode-light', {
      base: 'vs',
      inherit: true,
      rules: [],
      colors: {
        'editor.background': '#ffffff',
        'editor.foreground': '#000000',
        'editorLineNumber.foreground': '#237893',
        'editorLineNumber.activeForeground': '#0B216F',
        'editor.selectionBackground': '#ADD6FF',
        'editor.inactiveSelectionBackground': '#E5EBF1'
      }
    })

    // 设置主题
    monaco.editor.setTheme(isDark ? 'antcode-dark' : 'antcode-light')

    // 配置语言特性
    configureLanguageFeatures(monaco, language)

    // 如果没有初始值且有占位符，显示模板
    if (!value && placeholder) {
      editor.setValue(getDefaultTemplate(language))
    }

    // 调用外部的onMount回调
    onMount?.(editor, monaco)
  }

  // 配置语言特性（代码提示、语法检查等）
  const configureLanguageFeatures = (monaco: typeof import('monaco-editor'), lang: string) => {
    const monacoLang = getMonacoLanguage(lang)

    // Python特殊配置
    if (monacoLang === 'python') {
      // 配置Python代码提示
            monaco.languages.registerCompletionItemProvider('python', {
              provideCompletionItems: (model, position) => {
                const word = model.getWordUntilPosition(position)
                const range = {
                  startLineNumber: position.lineNumber,
                  endLineNumber: position.lineNumber,
                  startColumn: word.startColumn,
                  endColumn: word.endColumn,
                }

                const suggestions = [
                  {
                    label: 'print',
                    kind: monaco.languages.CompletionItemKind.Function,
                    insertText: 'print(${1:message})',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '打印输出',
                    range,
                  },
                  {
                    label: 'def',
                    kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: 'def ${1:function_name}(${2:params}):\n    ${3:pass}',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '定义函数',
                    range,
                  },
                  {
                    label: 'class',
                    kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: 'class ${1:ClassName}:\n    def __init__(self${2:, params}):\n        ${3:pass}',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '定义类',
                    range,
                  },
                  {
                    label: 'if',
                    kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: 'if ${1:condition}:\n    ${2:pass}',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '条件语句',
                    range,
                  },
                  {
                    label: 'for',
                    kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: 'for ${1:item} in ${2:iterable}:\n    ${3:pass}',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '循环语句',
                    range,
                  }
                ]

                return { suggestions }
              }
            })
    }

    // JavaScript/TypeScript特殊配置
    if (monacoLang === 'javascript' || monacoLang === 'typescript') {
            monaco.languages.registerCompletionItemProvider(monacoLang, {
              provideCompletionItems: (model, position) => {
                const word = model.getWordUntilPosition(position)
                const range = {
                  startLineNumber: position.lineNumber,
                  endLineNumber: position.lineNumber,
                  startColumn: word.startColumn,
                  endColumn: word.endColumn,
                }

                const suggestions = [
                  {
                    label: 'function',
                    kind: monaco.languages.CompletionItemKind.Keyword,
                    insertText: 'function ${1:functionName}(${2:params}) {\n    ${3:// code}\n}',
                    insertTextRules: monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet,
                    documentation: '定义函数',
                    range,
                  }
                ]

                return { suggestions }
              }
            })
    }
  }

  // 主题变化时更新编辑器主题
  useEffect(() => {
    if (editorRef.current) {
      const monacoGlobal = (window as { monaco?: typeof import('monaco-editor') }).monaco
      monacoGlobal?.editor.setTheme(isDark ? 'antcode-dark' : 'antcode-light')
    }
  }, [isDark])

  // 默认编辑器选项
  const defaultOptions: editor.IStandaloneEditorConstructionOptions = {
    minimap: { enabled: true },
    fontSize: 14,
    lineNumbers: 'on',
    roundedSelection: false,
    scrollBeyondLastLine: false,
    automaticLayout: true,
    tabSize: 4,
    insertSpaces: true,
    wordWrap: 'on',
    folding: true,
    foldingHighlight: true,
    showFoldingControls: 'always',
    bracketPairColorization: { enabled: true },
    guides: {
      bracketPairs: true,
      indentation: true
    },
    suggest: {
      showKeywords: true,
      showSnippets: true,
      showFunctions: true,
      showConstructors: true,
      showFields: true,
      showVariables: true,
      showClasses: true,
      showStructs: true,
      showInterfaces: true,
      showModules: true,
      showProperties: true,
      showEvents: true,
      showOperators: true,
      showUnits: true,
      showValues: true,
      showConstants: true,
      showEnums: true,
      showEnumMembers: true,
      showColors: true,
      showFiles: true,
      showReferences: true,
      showFolders: true,
      showTypeParameters: true
    },
    quickSuggestions: {
      other: true,
      comments: true,
      strings: true
    },
    parameterHints: { enabled: true },
    acceptSuggestionOnCommitCharacter: true,
    acceptSuggestionOnEnter: 'on',
    accessibilitySupport: 'auto',
    ...options
  }

  return (
    <div style={{ border: '1px solid #d9d9d9', borderRadius: '6px', overflow: 'hidden' }}>
      <Editor
        height={height}
        width={width}
        language={getMonacoLanguage(language)}
        value={value}
        onChange={onChange as OnChange}
        onMount={handleEditorDidMount}
        options={{
          ...defaultOptions,
          readOnly
        }}
        theme={isDark ? 'antcode-dark' : 'antcode-light'}
      />
    </div>
  )
}

export default CodeEditor
