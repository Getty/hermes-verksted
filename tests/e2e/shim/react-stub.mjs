// NOT FAITHFUL: there is no React renderer in the shim. createElement returns a plain descriptor;
// hooks are inert. A test that needs a rendered component cannot run on this shim.
export const Fragment = Symbol.for('react.fragment')
export function createElement(type, props, ...children) {
  return { $$typeof: 'vk-shim-element', type, props: props ?? {}, children }
}
export const useState = init => [typeof init === 'function' ? init() : init, () => {}]
export const useReducer = (_r, init) => [init, () => {}]
export const useEffect = () => {}
export const useLayoutEffect = () => {}
export const useMemo = fn => fn()
export const useCallback = fn => fn
export const useRef = init => ({ current: init })
export const useContext = () => undefined
export const useSyncExternalStore = (_sub, get) => get()
export const memo = c => c
export const createContext = d => ({ _default: d })
export default { createElement, Fragment, useState, useReducer, useEffect, useLayoutEffect, useMemo, useCallback, useRef, useContext, useSyncExternalStore, memo, createContext }
