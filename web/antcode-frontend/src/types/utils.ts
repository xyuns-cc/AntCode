/**
 * TypeScript 工具类型定义
 * 提供常用的类型工具和辅助函数
 */

import type React from 'react'

/**
 * 可选属性类型，将 T 中的指定属性 K 变为可选
 */
export type Optional<T, K extends keyof T> = Omit<T, K> & Partial<Pick<T, K>>

/**
 * 必需属性类型，将 T 中的指定属性 K 变为必需
 */
export type RequiredFields<T, K extends keyof T> = T & Required<Pick<T, K>>

/**
 * 深度可选类型
 */
export type DeepPartial<T> = {
  [P in keyof T]?: T[P] extends object ? DeepPartial<T[P]> : T[P]
}

/**
 * 深度只读类型
 */
export type DeepReadonly<T> = {
  readonly [P in keyof T]: T[P] extends object ? DeepReadonly<T[P]> : T[P]
}

/**
 * 非空类型，排除 null 和 undefined
 */
export type NonNullable<T> = T extends null | undefined ? never : T

/**
 * 键值对类型
 */
export type KeyValuePair<K extends string | number | symbol = string, V = unknown> = {
  [key in K]: V
}

/**
 * 函数类型
 */
export type Fn = (...args: unknown[]) => unknown
export type AsyncFn = (...args: unknown[]) => Promise<unknown>

/**
 * 组件Props类型
 */
export type ComponentProps<T = Record<string, never>> = T & {
  className?: string
  style?: React.CSSProperties
  children?: React.ReactNode
}

/**
 * 事件处理器类型
 */
export type EventHandler<T = Event> = (event: T) => void
export type ChangeHandler<T = unknown> = (value: T) => void

/**
 * API响应数据类型工具
 */
export type ApiResponseData<T> = T extends { data: infer D } ? D : T

/**
 * 提取数组元素类型
 */
export type ArrayElement<T> = T extends readonly (infer E)[] ? E : never

/**
 * 提取Promise resolve类型
 */
export type PromiseType<T> = T extends Promise<infer U> ? U : T

/**
 * 类型守卫函数
 */
export function isString(value: unknown): value is string {
  return typeof value === 'string'
}

export function isNumber(value: unknown): value is number {
  return typeof value === 'number' && !isNaN(value)
}

export function isBoolean(value: unknown): value is boolean {
  return typeof value === 'boolean'
}

export function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

export function isArray(value: unknown): value is unknown[] {
  return Array.isArray(value)
}

export function isFunction(value: unknown): value is Fn {
  return typeof value === 'function'
}

export function isDefined<T>(value: T | undefined | null): value is T {
  return value !== undefined && value !== null
}

/**
 * 类型断言函数
 */
export function assertIsString(value: unknown, message?: string): asserts value is string {
  if (!isString(value)) {
    throw new Error(message || `Expected string, got ${typeof value}`)
  }
}

export function assertIsNumber(value: unknown, message?: string): asserts value is number {
  if (!isNumber(value)) {
    throw new Error(message || `Expected number, got ${typeof value}`)
  }
}

export function assertIsObject(value: unknown, message?: string): asserts value is Record<string, unknown> {
  if (!isObject(value)) {
    throw new Error(message || `Expected object, got ${typeof value}`)
  }
}
