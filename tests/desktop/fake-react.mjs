// Stand-in for 'react': elements as plain objects, enough to render the components by hand.
export const createElement = (type, props, ...children) => ({ type, props: props || {}, children })
export const useSyncExternalStore = (subscribe, getSnapshot) => getSnapshot()
export const useState = initial => [initial, () => {}]
export const useEffect = () => {}
