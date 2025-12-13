/**
 * 编程语言配置
 */

import { APP_BRAND_NAME } from '@/config/app'

export interface LanguageConfig {
  id: string
  name: string
  monacoId: string
  extensions: string[]
  icon: string  // 保留用于兼容性，但推荐使用FileIcon组件
  color: string
  defaultTemplate: string
  snippets: CodeSnippet[]
}

export interface CodeSnippet {
  label: string
  insertText: string
  documentation: string
  kind: 'function' | 'keyword' | 'class' | 'variable' | 'snippet'
}

// 支持的编程语言配置
export const SUPPORTED_LANGUAGES: LanguageConfig[] = [
  {
    id: 'python',
    name: 'Python',
    monacoId: 'python',
    extensions: ['.py', '.pyw'],
    icon: '🐍',
    color: '#3776ab',
    defaultTemplate: `#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
"""

import os
import sys
import json
import requests
from typing import List, Dict, Any, Optional

def main():
    """主函数"""
    print("Hello, ${APP_BRAND_NAME}!")
    
    # 示例：处理数据
    data = {"message": "Hello World", "status": "success"}
    process_data(data)
    
    # 示例：网络请求
    # response = requests.get("https://api.example.com/data")
    # print(response.json())

def process_data(data: Dict[str, Any]) -> None:
    """处理数据的示例函数"""
    print(f"处理数据: {data}")
    # 在这里添加你的数据处理逻辑

if __name__ == "__main__":
    main()
`,
    snippets: [
      {
        label: 'def',
        insertText: 'def ${1:function_name}(${2:params}) -> ${3:None}:\n    """${4:函数描述}"""\n    ${5:pass}',
        documentation: '定义函数',
        kind: 'keyword'
      },
      {
        label: 'class',
        insertText: 'class ${1:ClassName}:\n    """${2:类描述}"""\n    \n    def __init__(self${3:, params}):\n        ${4:pass}',
        documentation: '定义类',
        kind: 'keyword'
      },
      {
        label: 'if',
        insertText: 'if ${1:condition}:\n    ${2:pass}',
        documentation: '条件语句',
        kind: 'keyword'
      },
      {
        label: 'for',
        insertText: 'for ${1:item} in ${2:iterable}:\n    ${3:pass}',
        documentation: '循环语句',
        kind: 'keyword'
      },
      {
        label: 'try',
        insertText: 'try:\n    ${1:pass}\nexcept ${2:Exception} as e:\n    ${3:print(f"错误: {e}")}',
        documentation: '异常处理',
        kind: 'keyword'
      }
    ]
  },
  {
    id: 'javascript',
    name: 'JavaScript',
    monacoId: 'javascript',
    extensions: ['.js', '.mjs'],
    icon: '🟨',
    color: '#f7df1e',
    defaultTemplate: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

// 导入必要的模块
const fs = require('fs');
const path = require('path');
const axios = require('axios');

/**
 * 主函数
 */
async function main() {
    console.log("Hello, ${APP_BRAND_NAME}!");
    
    // 示例：处理数据
    const data = { message: "Hello World", status: "success" };
    processData(data);
    
    // 示例：异步操作
    try {
        // const response = await axios.get('https://api.example.com/data');
        // 处理响应数据
    } catch (error) {
        console.error('请求失败:', error.message);
    }
}

/**
 * 处理数据的示例函数
 * @param {Object} data - 要处理的数据
 */
function processData(data) {
    // 处理数据逻辑
    // 在这里添加你的数据处理逻辑
}

// 执行主函数
main().catch(console.error);
`,
    snippets: [
      {
        label: 'function',
        insertText: 'function ${1:functionName}(${2:params}) {\n    ${3:// code}\n}',
        documentation: '定义函数',
        kind: 'keyword'
      },
      {
        label: 'arrow',
        insertText: 'const ${1:functionName} = (${2:params}) => {\n    ${3:// code}\n}',
        documentation: '箭头函数',
        kind: 'snippet'
      },
      {
        label: 'async',
        insertText: 'async function ${1:functionName}(${2:params}) {\n    ${3:// code}\n}',
        documentation: '异步函数',
        kind: 'keyword'
      },
      {
        label: 'class',
        insertText: 'class ${1:ClassName} {\n    constructor(${2:params}) {\n        ${3:// constructor code}\n    }\n    \n    ${4:methodName}() {\n        ${5:// method code}\n    }\n}',
        documentation: '定义类',
        kind: 'keyword'
      }
    ]
  },
  {
    id: 'typescript',
    name: 'TypeScript',
    monacoId: 'typescript',
    extensions: ['.ts', '.tsx'],
    icon: '🔷',
    color: '#3178c6',
    defaultTemplate: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

// 类型定义
interface Config {
    name: string;
    version: string;
    debug?: boolean;
}

interface DataItem {
    id: number;
    name: string;
    status: 'active' | 'inactive';
}

/**
 * 主函数
 */
async function main(): Promise<void> {
    console.log("Hello, ${APP_BRAND_NAME}!");
    
    const config: Config = {
        name: "${APP_BRAND_NAME} Project",
        version: "1.0.0",
        debug: true
    };
    
    // 示例：处理数据
    const data: DataItem[] = [
        { id: 1, name: "Item 1", status: "active" },
        { id: 2, name: "Item 2", status: "inactive" }
    ];
    
    processData(data);
}

/**
 * 处理数据的示例函数
 */
function processData(data: DataItem[]): void {
    // 处理数据逻辑
    data.forEach(item => {
        // 处理项目逻辑
    });
}

// 执行主函数
main().catch(console.error);
`,
    snippets: [
      {
        label: 'interface',
        insertText: 'interface ${1:InterfaceName} {\n    ${2:property}: ${3:type};\n}',
        documentation: '定义接口',
        kind: 'keyword'
      },
      {
        label: 'type',
        insertText: 'type ${1:TypeName} = ${2:type};',
        documentation: '定义类型别名',
        kind: 'keyword'
      },
      {
        label: 'function',
        insertText: 'function ${1:functionName}(${2:params}): ${3:returnType} {\n    ${4:// code}\n}',
        documentation: '定义函数',
        kind: 'keyword'
      }
    ]
  },
  {
    id: 'java',
    name: 'Java',
    monacoId: 'java',
    extensions: ['.java'],
    icon: '☕',
    color: '#ed8b00',
    defaultTemplate: `/**
 * 项目描述：请在这里描述你的项目功能
 * 作者：Your Name
 * 创建时间：${new Date().toLocaleDateString()}
 */

import java.util.*;
import java.io.*;
import java.net.http.*;

public class Main {
    
    /**
     * 主方法
     * @param args 命令行参数
     */
    public static void main(String[] args) {
        System.out.println("Hello, ${APP_BRAND_NAME}!");
        
        // 示例：处理数据
        Map<String, Object> data = new HashMap<>();
        data.put("message", "Hello World");
        data.put("status", "success");
        
        processData(data);
    }
    
    /**
     * 处理数据的示例方法
     * @param data 要处理的数据
     */
    public static void processData(Map<String, Object> data) {
        System.out.println("处理数据: " + data);
        // 在这里添加你的数据处理逻辑
    }
}
`,
    snippets: [
      {
        label: 'class',
        insertText: 'public class ${1:ClassName} {\n    ${2:// class body}\n}',
        documentation: '定义类',
        kind: 'keyword'
      },
      {
        label: 'method',
        insertText: 'public ${1:returnType} ${2:methodName}(${3:params}) {\n    ${4:// method body}\n}',
        documentation: '定义方法',
        kind: 'keyword'
      },
      {
        label: 'main',
        insertText: 'public static void main(String[] args) {\n    ${1:// main method body}\n}',
        documentation: '主方法',
        kind: 'snippet'
      }
    ]
  },
  {
    id: 'go',
    name: 'Go',
    monacoId: 'go',
    extensions: ['.go'],
    icon: '🐹',
    color: '#00add8',
    defaultTemplate: `/*
项目描述：请在这里描述你的项目功能
作者：Your Name
创建时间：${new Date().toLocaleDateString()}
*/

package main

import (
    "fmt"
    "encoding/json"
    "net/http"
    "log"
)

// 数据结构定义
type Config struct {
    Name    string \`json:"name"\`
    Version string \`json:"version"\`
    Debug   bool   \`json:"debug"\`
}

type DataItem struct {
    ID     int    \`json:"id"\`
    Name   string \`json:"name"\`
    Status string \`json:"status"\`
}

func main() {
    fmt.Println("Hello, ${APP_BRAND_NAME}!")
    
    // 示例：处理数据
    data := []DataItem{
        {ID: 1, Name: "Item 1", Status: "active"},
        {ID: 2, Name: "Item 2", Status: "inactive"},
    }
    
    processData(data)
}

// processData 处理数据的示例函数
func processData(data []DataItem) {
    fmt.Printf("处理数据: %+v\\n", data)
    for _, item := range data {
        fmt.Printf("处理项目: %s (状态: %s)\\n", item.Name, item.Status)
    }
}
`,
    snippets: [
      {
        label: 'func',
        insertText: 'func ${1:functionName}(${2:params}) ${3:returnType} {\n    ${4:// function body}\n}',
        documentation: '定义函数',
        kind: 'keyword'
      },
      {
        label: 'struct',
        insertText: 'type ${1:StructName} struct {\n    ${2:Field} ${3:Type} `json:"${4:field}"`\n}',
        documentation: '定义结构体',
        kind: 'keyword'
      },
      {
        label: 'if',
        insertText: 'if ${1:condition} {\n    ${2:// code}\n}',
        documentation: '条件语句',
        kind: 'keyword'
      }
    ]
  }
]

// 根据语言ID获取配置
export const getLanguageConfig = (languageId: string): LanguageConfig | undefined => {
  return SUPPORTED_LANGUAGES.find(lang => lang.id === languageId)
}

// 获取所有支持的语言选项（使用emoji图标，保持兼容性）
export const getLanguageOptions = () => {
  return SUPPORTED_LANGUAGES.map(lang => ({
    value: lang.id,
    label: `${lang.icon} ${lang.name}`,
    color: lang.color
  }))
}

// 获取所有支持的语言选项（使用新的FileIcon组件）
export const getLanguageOptionsWithIcons = () => {
  return SUPPORTED_LANGUAGES.map(lang => ({
    value: lang.id,
    label: lang.name,
    color: lang.color,
    extension: getExtensionForLanguage(lang.id)
  }))
}

// 根据语言ID获取对应的文件扩展名（用于FileIcon）
export const getExtensionForLanguage = (languageId: string): string => {
  const extensionMap: Record<string, string> = {
    'python': 'py',
    'javascript': 'js',
    'typescript': 'ts',
    'java': 'java',
    'go': 'go',
    'csharp': 'cs',
    'cpp': 'cpp',
    'c': 'c',
    'rust': 'rs',
    'php': 'php',
    'ruby': 'rb',
    'swift': 'swift',
    'kotlin': 'kt',
    'scala': 'scala',
    'r': 'r',
    'matlab': 'm',
    'perl': 'pl',
    'shell': 'sh',
    'powershell': 'ps1'
  }
  
  return extensionMap[languageId] || 'txt'
}
