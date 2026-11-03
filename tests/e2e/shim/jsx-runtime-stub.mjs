// NOT FAITHFUL: descriptors only (see react-stub.mjs).
export const Fragment = Symbol.for('react.fragment')
export const jsx = (type, props) => ({ $$typeof: 'vk-shim-element', type, props: props ?? {}, children: [] })
export const jsxs = jsx
