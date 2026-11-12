// 기기 판별: 휴대폰(작은 터치 화면)에서는 화질·입자 수를 낮추고 터치 조작을 쓴다.
export const TOUCH = typeof matchMedia === 'function' && matchMedia('(pointer: coarse)').matches;
export const PHONE = TOUCH && Math.min(screen.width, screen.height) <= 540;
export const isNarrow = () => innerWidth <= 860;
