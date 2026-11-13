// 게임 콘텐츠 표: 시대, 기술 발견, 직업, 산업(회사), 건물, 인생 목표

export const SKILLS = {
  farming: '농사·채집', construction: '건축', crafting: '제작', science: '과학', commerce: '상업',
  leadership: '리더십', medicine: '의술', art: '예술', teaching: '교육', athletics: '운동',
};

// 시대: tech = 이 시대에 들어서기 위한 누적 기술력
export const ERAS = [
  { id: 0, name: '원시 시대', tech: 0, leader: '족장', prod: 1.0, mort: 2.0, wage: 4, cityK: 1.0 },
  { id: 1, name: '농경 시대', tech: 600, leader: '촌장', prod: 1.35, mort: 1.7, wage: 6, cityK: 1.2 },
  { id: 2, name: '고대 문명', tech: 3800, leader: '왕', prod: 1.8, mort: 1.45, wage: 10, cityK: 1.5 },
  { id: 3, name: '중세', tech: 16500, leader: '국왕', prod: 2.4, mort: 1.3, wage: 16, cityK: 1.8 },
  { id: 4, name: '산업 혁명', tech: 50000, leader: '총리', prod: 3.5, mort: 1.1, wage: 30, cityK: 2.3 },
  { id: 5, name: '근대', tech: 165000, leader: '대통령', prod: 5, mort: 0.9, wage: 55, cityK: 3 },
  { id: 6, name: '정보화 시대', tech: 400000, leader: '대통령', prod: 7, mort: 0.75, wage: 90, cityK: 4 },
  { id: 7, name: '우주 시대', tech: 950000, leader: '대통령', prod: 10, mort: 0.6, wage: 140, cityK: 5 },
];

export const DISCOVERIES = [
  ['불의 사용', '석기 도구', '언어', '사냥 기술', '동굴 벽화', '매듭과 밧줄'],
  ['농업', '목축', '토기', '바퀴', '직조', '관개 수로'],
  ['문자', '화폐', '청동기', '수학', '건축술', '달력'],
  ['철기', '천문학', '인쇄술', '의학', '항해술', '시계'],
  ['증기기관', '전기', '철도', '화학', '백신', '전신'],
  ['전화', '자동차', '비행기', '라디오', '원자력', '항생제'],
  ['컴퓨터', '인터넷', '스마트폰', '인공지능', '로봇', '유전자 지도'],
  ['로켓', '인공위성', '우주정거장', '달 착륙', '화성 탐사', '핵융합'],
];

// 근무 시간표 (현지 시각). days: week=월~금, six=월~토, all=매일. 산업 시대 전에는 주 6일
export const SHIFTS = {
  self: { start: 7, end: 16, lunch: false, days: 'all', label: '해 뜨면 일하고 해 지기 전에' },
  farm: { start: 5.5, end: 17.5, lunch: true, days: 'all', label: '새벽부터 해 질 녘까지' },
  fish: { start: 4, end: 12, lunch: false, days: 'six', label: '새벽 출항' },
  site: { start: 7, end: 17, lunch: true, days: 'six', label: '현장 07~17시' },
  factory: { start: 8, end: 17, lunch: true, days: 'six', label: '공장 08~17시' },
  office: { start: 9, end: 18, lunch: true, days: 'week', label: '사무실 09~18시' },
  shop: { start: 9, end: 19, lunch: true, days: 'six', label: '가게 09~19시' },
  food: { start: 10, end: 21, lunch: false, days: 'six', label: '식당 10~21시' },
  school: { start: 8, end: 16.5, lunch: true, days: 'week', label: '학교 08~16:30' },
  care: { start: 8, end: 18, lunch: true, days: 'six', label: '병원 08~18시' },
  gov: { start: 9, end: 18, lunch: true, days: 'week', label: '관공서 09~18시' },
  free: { start: 10, end: 17, lunch: true, days: 'week', label: '자유롭게' },
  // 버스 기사: 첫차부터 막차까지 두 조로 나눠 탄다 (점심은 배고플 때 차고지에서)
  busEarly: { start: 5.5, end: 14, lunch: false, days: 'all', label: '버스 오전조 05:30~14:00' },
  busLate: { start: 13.5, end: 23.5, lunch: false, days: 'all', label: '버스 오후조 13:30~23:30' },
};

// 직업. self = 고용주 없이 스스로 하는 일. out: food/tech/build/money/art/heal/teach/gov/bricks(건자재)/ore/steel
// edu = 필요한 학력(0~4), shift = 근무 시간표
const J = (name, era, skill, out, rate, fit, extra = {}) => ({ name, era, skill, out, rate, fit, ...extra });
export const JOBS = {
  // 원시·자급
  fisher: J('어부', 0, 'farming', 'food', 2.5, { ath: .3, C: .2, N: -.2 }, { self: true, water: true, shift: 'fish' }),
  gatherer: J('채집꾼', 0, 'farming', 'food', 2.2, { ath: .3, C: .3 }, { self: true, maxEra: 1, shift: 'self' }),
  hunter: J('사냥꾼', 0, 'athletics', 'food', 2.6, { ath: .7, N: -.2 }, { self: true, maxEra: 1, shift: 'self' }),
  toolmaker: J('도구 장인', 0, 'crafting', 'tech', 0.9, { int: .3, cre: .4, C: .2 }, { self: true, maxEra: 1, shift: 'self' }),
  storyteller: J('이야기꾼', 0, 'art', 'art', 1, { cre: .5, E: .4, cha: .3 }, { self: true, maxEra: 2, shift: 'free' }),
  shaman: J('주술사', 0, 'medicine', 'heal', 1, { int: .3, O: .4, cha: .2 }, { self: true, maxEra: 2, shift: 'free' }),
  peasant: J('자영농', 2, 'farming', 'food', 2.6, { C: .3, ath: .2 }, { self: true, shift: 'farm' }),
  laborer: J('일용직', 2, 'construction', 'build', 0.8, { ath: .3 }, { self: true, shift: 'site' }),
  // 농업·자원
  farmer: J('농부', 1, 'farming', 'food', 4, { C: .4, ath: .3, A: .1 }, { shift: 'farm' }),
  lumberjack: J('벌목꾼', 1, 'athletics', 'bricks', 2.2, { ath: .6, C: .2 }, { shift: 'site' }),
  brickmaker: J('벽돌공', 1, 'crafting', 'bricks', 3, { ath: .3, C: .4 }, { shift: 'factory' }),
  miner: J('광부', 2, 'athletics', 'ore', 2.5, { ath: .6, N: -.3 }, { shift: 'site' }),
  sailor: J('선원', 2, 'athletics', 'food', 3.2, { ath: .5, N: -.2, O: .1 }, { shift: 'fish' }),
  // 건설
  builder: J('목수', 1, 'construction', 'build', 1, { ath: .4, C: .4 }, { shift: 'site' }),
  // 제작·상업
  craftsman: J('장인', 1, 'crafting', 'money', 1, { cre: .4, C: .4 }, { shift: 'shop' }),
  merchant: J('상인', 2, 'commerce', 'money', 1.2, { E: .5, cha: .5, int: .2 }, { edu: 1, shift: 'shop' }),
  blacksmith: J('대장장이', 2, 'crafting', 'money', 1.1, { ath: .5, C: .3 }, { shift: 'shop' }),
  cook: J('요리사', 2, 'crafting', 'money', 1.2, { cre: .4, C: .3, ath: .1 }, { shift: 'food' }),
  server: J('종업원', 2, 'commerce', 'money', 0.9, { E: .4, A: .4 }, { shift: 'food' }),
  tailor: J('재봉사', 2, 'crafting', 'money', 1, { cre: .4, C: .4 }, { shift: 'shop' }),
  baker: J('제빵사', 3, 'crafting', 'money', 1.1, { cre: .3, C: .5 }, { shift: 'food' }),
  barista: J('바리스타', 5, 'commerce', 'money', 1, { E: .3, cre: .3, A: .2 }, { shift: 'food' }),
  clerk: J('점원', 5, 'commerce', 'money', 0.9, { A: .3, C: .3 }, { shift: 'shop' }),
  hairdresser: J('미용사', 4, 'art', 'money', 1.1, { cre: .4, E: .3, cha: .2 }, { shift: 'shop' }),
  hotelier: J('호텔리어', 4, 'commerce', 'money', 1.2, { E: .4, cha: .4, A: .2 }, { edu: 2, shift: 'food' }),
  driver: J('운전기사', 4, 'athletics', 'money', 1.1, { C: .4, N: -.2 }, { shift: 'factory' }),
  // 공업
  steelworker: J('제철공', 4, 'crafting', 'steel', 2, { ath: .5, C: .3, N: -.2 }, { shift: 'factory' }),
  welder: J('용접공', 4, 'crafting', 'money', 1.6, { ath: .3, C: .5 }, { shift: 'factory' }),
  machinist: J('기계공', 4, 'crafting', 'money', 1.6, { int: .3, C: .5 }, { shift: 'factory' }),
  worker: J('공장 노동자', 4, 'crafting', 'money', 1.5, { C: .4, ath: .3 }, { shift: 'factory' }),
  assembler: J('조립 기술자', 5, 'crafting', 'money', 1.7, { C: .5, int: .2 }, { edu: 2, shift: 'factory' }),
  engineer: J('엔지니어', 4, 'science', 'tech', 1.5, { int: .6, C: .3 }, { edu: 4, shift: 'office' }),
  chemist: J('화학자', 5, 'science', 'tech', 1.8, { int: .7, C: .3 }, { edu: 4, shift: 'office' }),
  operator: J('발전소 기사', 5, 'science', 'gov', 1, { C: .5, int: .3 }, { edu: 3, shift: 'factory' }),
  // 의료·교육·공공
  doctor: J('의사', 3, 'medicine', 'heal', 1.4, { int: .5, A: .4, C: .3 }, { edu: 4, shift: 'care' }),
  nurse: J('간호사', 4, 'medicine', 'heal', 1.2, { A: .5, C: .4 }, { edu: 3, shift: 'care' }),
  teacher: J('교사', 2, 'teaching', 'teach', 1, { A: .4, E: .3, int: .3 }, { edu: 3, shift: 'school' }),
  professor: J('교수', 4, 'science', 'tech', 2.6, { int: .7, O: .4, E: .1 }, { edu: 4, shift: 'office' }),
  scholar: J('학자', 2, 'science', 'tech', 2, { int: .7, O: .5 }, { edu: 2, shift: 'free' }),
  priest: J('사제', 2, 'leadership', 'heal', 1, { A: .4, cha: .3, C: .2 }, { edu: 1, maxEra: 4, shift: 'free' }),
  official: J('공무원', 2, 'leadership', 'gov', 1, { C: .5, int: .3, cha: .2 }, { edu: 1, shift: 'gov' }), // 9급 공채는 학력 제한이 없다 — 읽고 쓸 줄만 알면
  soldier: J('병사', 2, 'athletics', 'gov', 1, { ath: .7, C: .2, A: -.2 }, { maxEra: 3, shift: 'gov' }),
  police: J('경찰관', 4, 'athletics', 'gov', 1, { ath: .4, C: .4, N: -.3 }, { edu: 3, shift: 'gov' }),
  firefighter: J('소방관', 4, 'athletics', 'gov', 1, { ath: .6, A: .3, N: -.3 }, { edu: 2, shift: 'gov' }),
  postman: J('집배원', 4, 'athletics', 'gov', 1, { C: .5, ath: .3 }, { shift: 'gov' }),
  stationmaster: J('역무원', 4, 'commerce', 'gov', 1, { C: .5, A: .2 }, { edu: 2, shift: 'gov' }),
  scientist: J('과학자', 5, 'science', 'tech', 3, { int: .8, O: .5 }, { edu: 4, shift: 'office' }),
  // 문화·지식 산업
  artist: J('예술가', 3, 'art', 'art', 1.2, { cre: .7, O: .5 }, { shift: 'free' }),
  banker: J('은행원', 4, 'commerce', 'money', 1.6, { int: .4, C: .5 }, { edu: 3, shift: 'office' }),
  journalist: J('기자', 5, 'art', 'money', 1.6, { E: .4, O: .4, int: .2 }, { edu: 3, shift: 'office' }),
  athlete: J('운동선수', 5, 'athletics', 'money', 2, { ath: 1, E: .2 }, { shift: 'free' }),
  programmer: J('프로그래머', 6, 'science', 'tech', 2.2, { int: .7, C: .3, E: -.1 }, { edu: 4, shift: 'office' }),
  designer: J('디자이너', 5, 'art', 'money', 2, { cre: .7, O: .4 }, { edu: 3, shift: 'office' }),
  astronaut: J('우주비행사', 7, 'athletics', 'tech', 3, { ath: .6, int: .5, N: -.4 }, { edu: 4, shift: 'office' }),
  rocketeer: J('로켓 엔지니어', 7, 'science', 'tech', 3.5, { int: .8, C: .4 }, { edu: 4, shift: 'office' }),
  // ── 현대 직업 (산업 시대 이후) ──
  // 의료·보건
  nurse_aide: J('간호조무사', 4, 'medicine', 'heal', 1, { A: .4, C: .3 }, { edu: 2, shift: 'care' }),
  pharmacist: J('약사', 4, 'medicine', 'heal', 1.1, { int: .4, C: .4 }, { edu: 4, shift: 'shop' }),
  dentist: J('치과의사', 4, 'medicine', 'heal', 1.4, { int: .4, C: .3, cre: .1 }, { edu: 4, shift: 'care' }),
  hygienist: J('치위생사', 5, 'medicine', 'heal', 1, { C: .4, A: .3 }, { edu: 3, shift: 'care' }),
  kmd: J('한의사', 3, 'medicine', 'heal', 1.2, { int: .4, A: .3 }, { edu: 4, shift: 'care' }),
  vet: J('수의사', 5, 'medicine', 'money', 1.3, { int: .3, A: .5 }, { edu: 4, shift: 'care' }),
  radiologist: J('방사선사', 5, 'medicine', 'heal', 1, { C: .4, int: .3 }, { edu: 3, shift: 'care' }),
  labtech: J('임상병리사', 5, 'science', 'heal', 1, { C: .5, int: .3 }, { edu: 3, shift: 'care' }),
  physio: J('물리치료사', 5, 'medicine', 'heal', 1, { ath: .3, A: .4 }, { edu: 3, shift: 'care' }),
  caregiver: J('요양보호사', 5, 'medicine', 'heal', 0.9, { A: .6, C: .2 }, { shift: 'care' }),
  social_worker: J('사회복지사', 5, 'teaching', 'heal', 0.9, { A: .6, E: .2 }, { edu: 3, shift: 'office' }),
  paramedic: J('구급대원', 5, 'medicine', 'gov', 1, { ath: .4, A: .3, N: -.3 }, { edu: 3, shift: 'gov' }),
  // 법률·금융·사무
  lawyer: J('변호사', 4, 'leadership', 'money', 2, { int: .5, cha: .3, E: .1 }, { edu: 4, shift: 'office' }),
  paralegal: J('법률사무원', 4, 'commerce', 'money', 1, { C: .5, int: .2 }, { edu: 3, shift: 'office' }),
  cpa: J('회계사', 4, 'commerce', 'money', 1.8, { int: .5, C: .4 }, { edu: 4, shift: 'office' }),
  tax_acct: J('세무사', 4, 'commerce', 'money', 1.6, { int: .4, C: .5 }, { edu: 4, shift: 'office' }),
  office_clerk: J('사무원', 4, 'commerce', 'money', 1, { C: .5, A: .1 }, { edu: 3, shift: 'office' }),
  marketer: J('마케터', 5, 'commerce', 'money', 1.3, { O: .3, cha: .3, cre: .3 }, { edu: 4, shift: 'office' }),
  salesrep: J('영업사원', 4, 'commerce', 'money', 1.3, { E: .5, cha: .4 }, { edu: 3, shift: 'office' }),
  hr: J('인사담당자', 5, 'leadership', 'money', 1.1, { A: .3, C: .3, cha: .2 }, { edu: 4, shift: 'office' }),
  broker: J('증권사 직원', 5, 'commerce', 'money', 1.8, { int: .4, N: -.3, E: .2 }, { edu: 4, shift: 'office' }),
  analyst: J('애널리스트', 5, 'science', 'money', 1.9, { int: .6, C: .3 }, { edu: 4, shift: 'office' }),
  insurer: J('보험설계사', 5, 'commerce', 'money', 1.2, { E: .5, cha: .5 }, { edu: 1, shift: 'office' }),
  realtor: J('공인중개사', 5, 'commerce', 'money', 1.2, { cha: .4, E: .3 }, { edu: 2, shift: 'shop' }),
  appraiser: J('감정평가사', 5, 'commerce', 'money', 1.6, { int: .5, C: .4 }, { edu: 4, shift: 'office' }),
  // IT·연구
  data_sci: J('데이터 사이언티스트', 6, 'science', 'tech', 2.4, { int: .7, O: .3 }, { edu: 4, shift: 'office' }),
  ai_eng: J('AI 엔지니어', 6, 'science', 'tech', 2.8, { int: .8, O: .3 }, { edu: 4, shift: 'office' }),
  planner: J('서비스 기획자', 6, 'commerce', 'money', 1.6, { O: .4, int: .3, cha: .2 }, { edu: 4, shift: 'office' }),
  qa: J('QA 엔지니어', 6, 'science', 'money', 1.3, { C: .6, int: .3 }, { edu: 3, shift: 'office' }),
  sec_eng: J('보안 전문가', 6, 'science', 'money', 1.8, { int: .6, C: .4 }, { edu: 4, shift: 'office' }),
  net_eng: J('네트워크 엔지니어', 6, 'science', 'money', 1.5, { int: .5, C: .3 }, { edu: 4, shift: 'office' }),
  call_agent: J('고객상담원', 6, 'commerce', 'money', 0.9, { A: .4, C: .3, N: -.2 }, { edu: 2, shift: 'office' }),
  game_dev: J('게임 개발자', 6, 'science', 'money', 1.8, { int: .5, cre: .4 }, { edu: 4, shift: 'office' }),
  game_designer: J('게임 기획자', 6, 'art', 'money', 1.5, { cre: .6, O: .3 }, { edu: 4, shift: 'office' }),
  semi_eng: J('반도체 엔지니어', 6, 'science', 'tech', 2.5, { int: .7, C: .3 }, { edu: 4, shift: 'office' }),
  fab_op: J('반도체 생산직', 6, 'crafting', 'money', 1.6, { C: .5, int: .2 }, { edu: 2, shift: 'factory' }),
  researcher: J('연구원', 5, 'science', 'tech', 2.2, { int: .6, O: .4 }, { edu: 4, shift: 'office' }),
  pharma_res: J('제약 연구원', 5, 'science', 'tech', 2, { int: .6, C: .3 }, { edu: 4, shift: 'office' }),
  // 건설·기술직
  rebar: J('철근공', 4, 'construction', 'build', 1.1, { ath: .6, C: .2 }, { shift: 'site' }),
  plasterer: J('미장공', 3, 'construction', 'build', 1, { ath: .4, C: .4 }, { shift: 'site' }),
  electrician: J('전기기사', 4, 'construction', 'build', 1, { int: .3, C: .4 }, { edu: 2, shift: 'site' }),
  plumber: J('배관공', 4, 'construction', 'build', 1, { ath: .3, C: .4 }, { edu: 1, shift: 'site' }),
  crane_op: J('타워크레인 기사', 5, 'construction', 'build', 1.3, { C: .5, N: -.4 }, { edu: 2, shift: 'site' }),
  excavator: J('굴착기 기사', 5, 'construction', 'build', 1.2, { C: .4, ath: .2 }, { edu: 1, shift: 'site' }),
  site_mgr: J('현장소장', 4, 'leadership', 'build', 1.2, { C: .4, cha: .3, E: .2 }, { edu: 3, shift: 'site' }),
  civil_eng: J('토목기사', 4, 'science', 'build', 1.1, { int: .4, C: .4 }, { edu: 4, shift: 'site' }),
  architect: J('건축가', 4, 'art', 'money', 1.6, { cre: .5, int: .4 }, { edu: 4, shift: 'office' }),
  interior: J('인테리어 디자이너', 5, 'art', 'money', 1.3, { cre: .6, O: .2 }, { edu: 3, shift: 'office' }),
  mechanic: J('자동차 정비사', 5, 'crafting', 'money', 1.2, { C: .4, int: .2, ath: .2 }, { edu: 2, shift: 'shop' }),
  painter: J('도장공', 4, 'crafting', 'money', 1.2, { C: .4, ath: .3 }, { shift: 'factory' }),
  forklift: J('지게차 운전원', 5, 'athletics', 'money', 1, { C: .4, ath: .2 }, { edu: 1, shift: 'factory' }),
  quality: J('품질관리원', 5, 'science', 'money', 1.2, { C: .6, int: .2 }, { edu: 3, shift: 'factory' }),
  // 운송
  bus_driver: J('버스기사', 5, 'athletics', 'money', 1.1, { C: .4, N: -.3, A: .2 }, { edu: 0, shift: 'bus' }), // 학력보다 대형 면허
  taxi_driver: J('택시기사', 5, 'athletics', 'money', 1, { E: .3, C: .2 }, { edu: 1, shift: 'shop' }),
  courier: J('택배기사', 5, 'athletics', 'money', 1.2, { ath: .5, C: .3 }, { shift: 'factory' }),
  rider: J('배달 라이더', 6, 'athletics', 'money', 1.1, { ath: .5, N: -.2 }, { self: true, shift: 'food' }),
  pilot: J('항공기 조종사', 5, 'science', 'money', 2.5, { int: .4, C: .4, N: -.4 }, { edu: 4, shift: 'office' }),
  attendant: J('항공 승무원', 5, 'commerce', 'money', 1.3, { cha: .4, A: .3, ath: .2 }, { edu: 3, shift: 'office' }),
  air_mech: J('항공정비사', 5, 'crafting', 'money', 1.4, { C: .5, int: .3 }, { edu: 3, shift: 'factory' }),
  captain: J('선장', 4, 'leadership', 'money', 1.6, { C: .3, cha: .3, N: -.3 }, { edu: 3, shift: 'fish' }),
  navigator: J('항해사', 4, 'science', 'money', 1.4, { int: .4, C: .3 }, { edu: 3, shift: 'fish' }),
  train_driver: J('철도 기관사', 4, 'athletics', 'gov', 1, { C: .5, N: -.3 }, { edu: 3, shift: 'gov' }),
  // 판매·서비스
  cashier: J('마트 계산원', 5, 'commerce', 'money', 0.9, { C: .4, A: .2 }, { shift: 'shop' }),
  store_mgr: J('점장', 5, 'leadership', 'money', 1.2, { C: .4, cha: .3 }, { edu: 2, shift: 'shop' }),
  cvs_part: J('편의점 아르바이트', 6, 'commerce', 'money', 0.8, { C: .3 }, { shift: 'shop' }),
  sales_clerk: J('백화점 판매원', 5, 'commerce', 'money', 1, { cha: .4, E: .3 }, { edu: 1, shift: 'shop' }),
  nail: J('네일아티스트', 6, 'art', 'money', 1, { cre: .4, C: .3 }, { shift: 'shop' }),
  makeup: J('메이크업 아티스트', 5, 'art', 'money', 1.1, { cre: .5, cha: .2 }, { edu: 1, shift: 'shop' }),
  trainer: J('헬스 트레이너', 5, 'athletics', 'money', 1.1, { ath: .6, E: .3 }, { edu: 1, shift: 'shop' }),
  janitor: J('청소원', 5, 'athletics', 'money', 0.8, { C: .4, A: .2 }, { shift: 'factory' }),
  guard: J('경비원', 5, 'athletics', 'money', 0.8, { C: .4, N: -.2 }, { shift: 'factory' }),
  florist: J('플로리스트', 5, 'art', 'money', 1, { cre: .5, A: .2 }, { edu: 1, shift: 'shop' }),
  chef: J('셰프', 5, 'crafting', 'money', 1.4, { cre: .4, C: .4 }, { edu: 2, shift: 'food' }),
  // 교육·돌봄
  tutor: J('학원 강사', 5, 'teaching', 'teach', 1.2, { int: .4, cha: .3, E: .2 }, { edu: 4, shift: 'free' }),
  childcare: J('보육교사', 5, 'teaching', 'teach', 1, { A: .6, C: .2 }, { edu: 3, shift: 'school' }),
  kinder_teacher: J('유치원 교사', 5, 'teaching', 'teach', 1, { A: .5, E: .3 }, { edu: 4, shift: 'school' }),
  nutritionist: J('영양사', 5, 'medicine', 'money', 1, { C: .5, int: .2 }, { edu: 4, shift: 'school' }),
  sanitation: J('환경미화원', 4, 'athletics', 'gov', 1, { ath: .4, C: .4 }, { shift: 'site' }),
  // 방송·문화·예술
  pd: J('PD', 5, 'art', 'money', 1.6, { cre: .5, cha: .3, E: .2 }, { edu: 4, shift: 'office' }),
  announcer: J('아나운서', 5, 'art', 'money', 1.5, { cha: .6, int: .2 }, { edu: 4, shift: 'office' }),
  tv_writer: J('방송작가', 5, 'art', 'money', 1.2, { cre: .6, O: .3 }, { edu: 3, shift: 'free' }),
  camera: J('촬영감독', 5, 'art', 'money', 1.3, { cre: .4, ath: .2 }, { edu: 3, shift: 'free' }),
  singer: J('가수', 5, 'art', 'art', 1.5, { cha: .5, cre: .4, E: .3 }, { shift: 'free' }),
  actor: J('배우', 5, 'art', 'art', 1.5, { cha: .6, cre: .3, E: .2 }, { shift: 'free' }),
  ent_mgr: J('연예 매니저', 6, 'commerce', 'money', 1.1, { E: .4, C: .3 }, { edu: 2, shift: 'free' }),
  editor: J('편집자', 4, 'art', 'money', 1.1, { int: .4, C: .4 }, { edu: 4, shift: 'office' }),
  author: J('작가', 3, 'art', 'art', 1.2, { cre: .6, O: .4, E: -.2 }, { self: true, edu: 2, shift: 'free' }),
  webtoonist: J('웹툰 작가', 6, 'art', 'art', 1.6, { cre: .7, C: .2 }, { self: true, edu: 1, shift: 'free' }),
  creator: J('유튜버', 6, 'art', 'art', 1.5, { cha: .5, cre: .4, E: .3 }, { self: true, shift: 'free' }),
  copywriter: J('카피라이터', 5, 'art', 'money', 1.3, { cre: .6, int: .2 }, { edu: 4, shift: 'office' }),
  coach: J('코치', 5, 'leadership', 'money', 1.3, { ath: .4, cha: .3 }, { edu: 2, shift: 'free' }),
  // 농림어업
  rancher: J('축산업자', 2, 'farming', 'food', 3, { ath: .3, C: .4 }, { self: true, shift: 'farm' }),
};
// ── 연봉표 (세전, 만원, 2026년 한국 기준의 대략적인 중간값) ──
// 참고: 2026 최저임금 시급 10,320원(월 2,156,880원), 2025 상용근로자 평균 연봉 5,061만원(경총), 근로자 중위 연봉 약 3,417만원(국세청 2024 귀속),
// 의사 연평균 보수 약 2.85억원(보건복지부, 개원의 포함), 소프트웨어 개발 평균 5,526만원(잡플래닛 2025), 공무원·교원 보수표, 각종 채용 공고 수준.
// 게임 속 연봉은 '정보화 시대(=오늘날)'에 이 값이고, 그 이전 시대엔 시대의 생산성(임금 수준)만큼 줄어든다(현재 가치로 환산).
// 실제 앤의 월급은 숙련도·직급·회사 형편(구인난이면 웃돈)에 따라 ±30% 정도 달라진다.
export const PAY = {
  // 전통·자급
  fisher: [3000, '농림어업'], gatherer: [1000, '농림어업'], hunter: [1200, '농림어업'], toolmaker: [1500, '제조·생산'], storyteller: [1200, '방송·문화·예술'],
  shaman: [1800, '종교'], peasant: [2400, '농림어업'], laborer: [3900, '건설'], farmer: [3000, '농림어업'], lumberjack: [3800, '농림어업'], rancher: [4000, '농림어업'],
  brickmaker: [3500, '제조·생산'], miner: [5500, '제조·생산'], sailor: [5000, '농림어업'], builder: [5200, '건설'], craftsman: [3600, '제조·생산'],
  merchant: [4000, '판매·서비스'], blacksmith: [3800, '제조·생산'], cook: [3300, '판매·서비스'], server: [2600, '판매·서비스'], tailor: [2800, '제조·생산'],
  baker: [2900, '판매·서비스'], barista: [2600, '판매·서비스'], clerk: [2600, '판매·서비스'], hairdresser: [3000, '판매·서비스'], hotelier: [3600, '판매·서비스'],
  driver: [5000, '운송'], steelworker: [7000, '제조·생산'], welder: [5500, '제조·생산'], machinist: [4800, '제조·생산'], worker: [3900, '제조·생산'],
  assembler: [6500, '제조·생산'], engineer: [6500, 'IT·연구'], chemist: [6800, 'IT·연구'], operator: [7000, '공공·안전'],
  doctor: [20000, '의료·보건'], nurse: [4700, '의료·보건'], teacher: [5500, '교육·돌봄'], professor: [10500, '교육·돌봄'], scholar: [4800, 'IT·연구'],
  priest: [2800, '종교'], official: [5500, '공공·안전'], soldier: [2400, '공공·안전'], police: [6000, '공공·안전'], firefighter: [6000, '공공·안전'],
  postman: [4500, '공공·안전'], stationmaster: [5800, '운송'], scientist: [8000, 'IT·연구'], artist: [1800, '방송·문화·예술'], banker: [9500, '법률·금융·사무'],
  journalist: [5500, '방송·문화·예술'], athlete: [8000, '스포츠'], programmer: [5500, 'IT·연구'], designer: [3800, '방송·문화·예술'],
  astronaut: [12000, 'IT·연구'], rocketeer: [9000, 'IT·연구'],
  // 의료·보건
  nurse_aide: [2800, '의료·보건'], pharmacist: [7500, '의료·보건'], dentist: [17000, '의료·보건'], hygienist: [3300, '의료·보건'], kmd: [11000, '의료·보건'],
  vet: [7000, '의료·보건'], radiologist: [4800, '의료·보건'], labtech: [4500, '의료·보건'], physio: [3800, '의료·보건'], caregiver: [2500, '의료·보건'],
  social_worker: [3300, '교육·돌봄'], paramedic: [5800, '공공·안전'],
  // 법률·금융·사무
  lawyer: [12000, '법률·금융·사무'], paralegal: [3600, '법률·금융·사무'], cpa: [9000, '법률·금융·사무'], tax_acct: [8000, '법률·금융·사무'],
  office_clerk: [3400, '법률·금융·사무'], marketer: [4800, '법률·금융·사무'], salesrep: [4600, '법률·금융·사무'], hr: [5000, '법률·금융·사무'],
  broker: [11000, '법률·금융·사무'], analyst: [12000, '법률·금융·사무'], insurer: [4000, '법률·금융·사무'], realtor: [4200, '판매·서비스'], appraiser: [9500, '법률·금융·사무'],
  // IT·연구
  data_sci: [7000, 'IT·연구'], ai_eng: [8500, 'IT·연구'], planner: [5500, 'IT·연구'], qa: [4500, 'IT·연구'], sec_eng: [6500, 'IT·연구'], net_eng: [5800, 'IT·연구'],
  call_agent: [2800, '판매·서비스'], game_dev: [5800, 'IT·연구'], game_designer: [4800, 'IT·연구'], semi_eng: [9500, 'IT·연구'], fab_op: [5500, '제조·생산'],
  researcher: [7000, 'IT·연구'], pharma_res: [6500, 'IT·연구'],
  // 건설·기술직
  rebar: [5000, '건설'], plasterer: [4800, '건설'], electrician: [5200, '건설'], plumber: [4800, '건설'], crane_op: [8000, '건설'], excavator: [5800, '건설'],
  site_mgr: [8500, '건설'], civil_eng: [6000, '건설'], architect: [5500, '건설'], interior: [4200, '건설'], mechanic: [4200, '제조·생산'], painter: [4600, '제조·생산'],
  forklift: [3800, '제조·생산'], quality: [4500, '제조·생산'],
  // 운송
  bus_driver: [5200, '운송'], taxi_driver: [3200, '운송'], courier: [5000, '운송'], rider: [3800, '운송'], pilot: [15000, '운송'], attendant: [5000, '운송'],
  air_mech: [6500, '운송'], captain: [9500, '운송'], navigator: [6500, '운송'], train_driver: [7500, '운송'],
  // 판매·서비스
  cashier: [2600, '판매·서비스'], store_mgr: [4200, '판매·서비스'], cvs_part: [2600, '판매·서비스'], sales_clerk: [3000, '판매·서비스'], nail: [2700, '판매·서비스'],
  makeup: [3000, '판매·서비스'], trainer: [3200, '스포츠'], janitor: [2600, '판매·서비스'], guard: [2700, '판매·서비스'], florist: [2700, '판매·서비스'], chef: [4800, '판매·서비스'],
  // 교육·돌봄·공공
  tutor: [3800, '교육·돌봄'], childcare: [2900, '교육·돌봄'], kinder_teacher: [3500, '교육·돌봄'], nutritionist: [3600, '교육·돌봄'], sanitation: [5500, '공공·안전'],
  // 방송·문화·예술
  pd: [6000, '방송·문화·예술'], announcer: [6500, '방송·문화·예술'], tv_writer: [3500, '방송·문화·예술'], camera: [4500, '방송·문화·예술'], singer: [3000, '방송·문화·예술'],
  actor: [3000, '방송·문화·예술'], ent_mgr: [3200, '방송·문화·예술'], editor: [3800, '방송·문화·예술'], author: [2000, '방송·문화·예술'], webtoonist: [4500, '방송·문화·예술'],
  creator: [3000, '방송·문화·예술'], copywriter: [4500, '방송·문화·예술'], coach: [5500, '스포츠'],
};
for (const k in JOBS) { const p = PAY[k]; JOBS[k].pay = p ? p[0] : 3600; JOBS[k].cat = p ? p[1] : '기타'; }


// 민간 산업(회사). k = 종사자 1명이 감당하는 인구(수요). needs = 지형 조건, requires = 나라에 먼저 있어야 하는 산업
export const INDUSTRIES = {
  farm: { name: '농장', era: 1, building: 'farm', jobs: ['farmer'], k: 5, suffix: ['농장', '농원', '영농'] },
  lumber: { name: '목재소', era: 1, building: 'lumberyard', jobs: ['lumberjack'], k: 30, needs: 'forest', suffix: ['목재', '제재소', '임업'] },
  brickworks: { name: '벽돌·기와', era: 1, building: 'kiln', jobs: ['brickmaker'], k: 24, suffix: ['벽돌', '기와', '요업', '건자재'] },
  construction: { name: '건설', era: 1, building: 'workshop', jobs: ['builder', 'rebar', 'plasterer', 'electrician', 'plumber', 'crane_op', 'excavator', 'site_mgr', 'civil_eng'], mix: { builder: 0.3, rebar: 0.15, plasterer: 0.12, electrician: 0.1, plumber: 0.08, crane_op: 0.05, excavator: 0.06, site_mgr: 0.06, civil_eng: 0.08 }, k: 7, suffix: ['건설', '건축', '종합건설'] },
  workshop: { name: '공방', era: 1, building: 'workshop', jobs: ['craftsman'], k: 10, suffix: ['공방', '공예', '도자기'] },
  restaurant: { name: '식당', era: 2, building: 'restaurant', jobs: ['cook', 'server'], k: 14, suffix: ['식당', '밥집', '국밥', '반점'] },
  trade: { name: '상회', era: 2, building: 'market', jobs: ['merchant'], k: 12, suffix: ['상회', '상점', '무역'] },
  tailor: { name: '옷가게', era: 2, building: 'tailorshop', jobs: ['tailor'], k: 28, suffix: ['양복점', '옷가게', '의류'] },
  smithy: { name: '대장간', era: 2, building: 'forge', jobs: ['blacksmith'], k: 18, suffix: ['대장간', '철공소'] },
  mining: { name: '광산', era: 2, building: 'mine', jobs: ['miner'], k: 22, needs: 'mountain', suffix: ['광업', '탄광', '광산'] },
  fishery: { name: '수산', era: 2, building: 'dock', jobs: ['sailor'], k: 12, needs: 'water', suffix: ['수산', '선단', '어업'] },
  bakery: { name: '빵집', era: 3, building: 'bakery', jobs: ['baker'], k: 30, suffix: ['베이커리', '제과', '빵집'] },
  clinic: { name: '의원', era: 3, building: 'clinic', jobs: ['doctor', 'nurse', 'nurse_aide'], mix: { doctor: 0.3, nurse: 0.4, nurse_aide: 0.3 }, k: 18, suffix: ['의원', '병원', '메디컬'] },
  studio: { name: '예술 공방', era: 3, building: 'studio', jobs: ['artist'], k: 26, suffix: ['화방', '스튜디오', '아틀리에'] },
  steel: { name: '제철', era: 4, building: 'steelmill', jobs: ['steelworker', 'engineer', 'forklift', 'office_clerk'], mix: { steelworker: 0.65, engineer: 0.15, forklift: 0.1, office_clerk: 0.1 }, k: 18, requires: 'mining', suffix: ['제철', '철강', '스틸'] },
  shipbuilding: { name: '조선', era: 4, building: 'shipyard', jobs: ['welder', 'engineer', 'worker', 'painter', 'electrician'], mix: { welder: 0.35, engineer: 0.12, worker: 0.3, painter: 0.13, electrician: 0.1 }, k: 16, needs: 'sea', requires: 'steel', suffix: ['조선', '중공업', '해양'] },
  machinery: { name: '중공업', era: 4, building: 'heavyplant', jobs: ['machinist', 'engineer', 'worker', 'quality'], mix: { machinist: 0.4, engineer: 0.15, worker: 0.35, quality: 0.1 }, k: 16, requires: 'steel', suffix: ['중공업', '기계', '엔진'] },
  factory: { name: '경공업', era: 4, building: 'factory', jobs: ['worker', 'engineer', 'quality', 'forklift', 'office_clerk'], mix: { worker: 0.6, engineer: 0.1, quality: 0.1, forklift: 0.1, office_clerk: 0.1 }, k: 8, suffix: ['공업', '방직', '제작소'] },
  bank: { name: '은행', era: 4, building: 'bank', jobs: ['banker', 'office_clerk', 'salesrep'], mix: { banker: 0.6, office_clerk: 0.25, salesrep: 0.15 }, k: 35, suffix: ['은행', '금융', '증권'] },
  logistics: { name: '운수·물류', era: 4, building: 'warehouse', jobs: ['driver', 'worker', 'courier', 'forklift'], mix: { driver: 0.3, worker: 0.3, courier: 0.3, forklift: 0.1 }, k: 22, suffix: ['운수', '통운', '물류', '택배'] },
  hotel: { name: '호텔', era: 4, building: 'hotel', jobs: ['hotelier', 'cook', 'server', 'chef'], mix: { hotelier: 0.4, cook: 0.2, server: 0.3, chef: 0.1 }, k: 45, needs: 'scenic', suffix: ['호텔', '리조트', '여관'] },
  salon: { name: '미용실', era: 4, building: 'salon', jobs: ['hairdresser', 'nail', 'makeup'], mix: { hairdresser: 0.6, nail: 0.25, makeup: 0.15 }, k: 40, suffix: ['헤어', '미용실', '살롱'] },
  auto: { name: '자동차', era: 5, building: 'carplant', jobs: ['assembler', 'engineer', 'designer', 'quality', 'painter', 'hr'], mix: { hr: 0.03, assembler: 0.55, engineer: 0.15, designer: 0.05, quality: 0.12, painter: 0.13 }, k: 16, requires: 'steel', suffix: ['자동차', '모터스', '오토'] },
  chemical: { name: '화학', era: 5, building: 'chemplant', jobs: ['chemist', 'worker', 'quality'], mix: { chemist: 0.25, worker: 0.6, quality: 0.15 }, k: 30, suffix: ['화학', '케미칼', '정유'] },
  retail: { name: '슈퍼마켓', era: 5, building: 'supermarket', jobs: ['clerk', 'cashier', 'store_mgr'], mix: { clerk: 0.45, cashier: 0.45, store_mgr: 0.1 }, k: 22, suffix: ['마트', '슈퍼', '상사'] },
  cafe: { name: '카페', era: 5, building: 'cafe', jobs: ['barista'], k: 35, suffix: ['커피', '카페', '다방'] },
  media: { name: '언론사', era: 5, building: 'office', jobs: ['journalist', 'pd', 'announcer', 'tv_writer', 'camera'], mix: { journalist: 0.4, pd: 0.2, announcer: 0.1, tv_writer: 0.15, camera: 0.15 }, k: 45, suffix: ['신문', '일보', '방송'] },
  sports: { name: '스포츠 구단', era: 5, building: 'stadium', jobs: ['athlete', 'coach', 'trainer'], mix: { athlete: 0.7, coach: 0.15, trainer: 0.15 }, k: 50, suffix: ['FC', '스포츠', '구단'] },
  electronics: { name: '전자', era: 6, building: 'electronics', jobs: ['assembler', 'engineer', 'programmer', 'semi_eng', 'fab_op', 'quality'], mix: { assembler: 0.25, engineer: 0.15, programmer: 0.1, semi_eng: 0.15, fab_op: 0.3, quality: 0.05 }, k: 14, requires: 'chemical', suffix: ['전자', '반도체', '일렉트릭'] },
  tech: { name: 'IT 기업', era: 6, building: 'office', jobs: ['programmer', 'designer', 'data_sci', 'ai_eng', 'planner', 'qa', 'sec_eng', 'hr'], mix: { hr: 0.03, programmer: 0.45, designer: 0.12, data_sci: 0.1, ai_eng: 0.1, planner: 0.1, qa: 0.08, sec_eng: 0.05 }, k: 16, suffix: ['테크', '소프트', '랩스'] },
  aerospace: { name: '우주항공', era: 7, building: 'spacecenter', jobs: ['rocketeer', 'astronaut', 'engineer', 'researcher'], mix: { rocketeer: 0.35, astronaut: 0.1, engineer: 0.35, researcher: 0.2 }, k: 40, requires: 'electronics', suffix: ['우주항공', '스페이스', '로켓'] },
  // ── 현대 산업 (잡코리아 직무 분류를 참고해 넓혔다) ──
  pharmacy: { name: '약국', era: 4, building: 'pharmacy', jobs: ['pharmacist', 'cashier'], mix: { pharmacist: 0.6, cashier: 0.4 }, k: 25, suffix: ['약국', '온누리약국', '건강약국'] },
  dental: { name: '치과', era: 4, building: 'shop', jobs: ['dentist', 'hygienist', 'nurse_aide'], mix: { dentist: 0.35, hygienist: 0.4, nurse_aide: 0.25 }, k: 40, suffix: ['치과', '치과의원', '덴탈'] },
  oriental: { name: '한의원', era: 3, building: 'clinic', jobs: ['kmd', 'nurse_aide'], mix: { kmd: 0.5, nurse_aide: 0.5 }, k: 60, suffix: ['한의원', '한방병원'] },
  vetclinic: { name: '동물병원', era: 5, building: 'shop', jobs: ['vet', 'nurse_aide'], mix: { vet: 0.6, nurse_aide: 0.4 }, k: 90, suffix: ['동물병원', '펫클리닉'] },
  carehome: { name: '요양원', era: 5, building: 'carehome', jobs: ['caregiver', 'nurse', 'social_worker', 'cook'], mix: { caregiver: 0.6, nurse: 0.15, social_worker: 0.15, cook: 0.1 }, k: 30, suffix: ['요양원', '실버케어', '요양센터'] },
  daycare: { name: '어린이집', era: 5, building: 'shop', jobs: ['childcare', 'cook'], mix: { childcare: 0.8, cook: 0.2 }, k: 40, suffix: ['어린이집', '키즈', '아이마루'] },
  hagwon: { name: '학원', era: 5, building: 'bizbuilding', jobs: ['tutor', 'office_clerk'], mix: { tutor: 0.85, office_clerk: 0.15 }, k: 25, suffix: ['학원', '아카데미', '에듀'] },
  law: { name: '법률사무소', era: 4, building: 'bizbuilding', jobs: ['lawyer', 'paralegal'], mix: { lawyer: 0.5, paralegal: 0.5 }, k: 70, suffix: ['법률사무소', '법무법인', '로펌'] },
  accounting: { name: '회계·세무', era: 4, building: 'bizbuilding', jobs: ['cpa', 'tax_acct', 'office_clerk'], mix: { cpa: 0.4, tax_acct: 0.3, office_clerk: 0.3 }, k: 60, suffix: ['회계법인', '세무회계', '세무사무소'] },
  securities: { name: '증권', era: 5, building: 'office', jobs: ['broker', 'analyst', 'office_clerk'], mix: { broker: 0.55, analyst: 0.2, office_clerk: 0.25 }, k: 90, suffix: ['증권', '투자증권', '자산운용'] },
  insurance: { name: '보험', era: 5, building: 'office', jobs: ['insurer', 'office_clerk', 'call_agent'], mix: { insurer: 0.6, office_clerk: 0.25, call_agent: 0.15 }, k: 50, suffix: ['생명', '화재', '손해보험'] },
  realestate: { name: '부동산 중개', era: 5, building: 'shop', jobs: ['realtor', 'appraiser'], mix: { realtor: 0.85, appraiser: 0.15 }, k: 50, suffix: ['공인중개사', '부동산', '리얼티'] },
  architecture: { name: '건축사무소', era: 4, building: 'bizbuilding', jobs: ['architect', 'civil_eng', 'interior'], mix: { architect: 0.5, civil_eng: 0.25, interior: 0.25 }, k: 70, suffix: ['건축사사무소', '디자인그룹', '설계'] },
  game: { name: '게임 회사', era: 6, building: 'office', jobs: ['game_dev', 'game_designer', 'designer', 'qa'], mix: { game_dev: 0.45, game_designer: 0.2, designer: 0.2, qa: 0.15 }, k: 70, suffix: ['게임즈', '엔터테인먼트', '스튜디오'] },
  telecom: { name: '통신사', era: 6, building: 'office', jobs: ['net_eng', 'call_agent', 'salesrep', 'programmer'], mix: { net_eng: 0.3, call_agent: 0.35, salesrep: 0.2, programmer: 0.15 }, k: 70, suffix: ['텔레콤', '모바일', '통신'] },
  pharma: { name: '제약', era: 5, building: 'factory', jobs: ['pharma_res', 'worker', 'salesrep', 'quality'], mix: { pharma_res: 0.25, worker: 0.45, salesrep: 0.2, quality: 0.1 }, k: 40, requires: 'chemical', suffix: ['제약', '바이오', '약품'] },
  repair: { name: '자동차 정비소', era: 5, building: 'garage', jobs: ['mechanic'], k: 40, suffix: ['카센터', '모터스', '정비'] },
  bus: { name: '버스 회사', era: 5, building: 'busdepot', jobs: ['bus_driver', 'mechanic'], mix: { bus_driver: 0.85, mechanic: 0.15 }, k: 35, suffix: ['교통', '여객', '버스'] },
  taxi: { name: '택시 회사', era: 5, building: 'garage', jobs: ['taxi_driver'], k: 45, suffix: ['택시', '운수', '콜택시'] },
  airline: { name: '항공사', era: 5, building: 'airport', jobs: ['pilot', 'attendant', 'air_mech', 'office_clerk'], mix: { pilot: 0.15, attendant: 0.4, air_mech: 0.25, office_clerk: 0.2 }, k: 150, suffix: ['항공', '에어', '에어라인'] },
  shipping: { name: '해운', era: 4, building: 'dock', jobs: ['captain', 'navigator', 'sailor'], mix: { captain: 0.15, navigator: 0.25, sailor: 0.6 }, k: 60, needs: 'sea', suffix: ['해운', '상선', '마린'] },
  convenience: { name: '편의점', era: 6, building: 'cvs', jobs: ['cvs_part'], k: 22, suffix: ['편의점', '24', '마트24'] },
  mall: { name: '백화점', era: 5, building: 'mall', jobs: ['sales_clerk', 'cashier', 'store_mgr', 'marketer'], mix: { sales_clerk: 0.55, cashier: 0.2, store_mgr: 0.1, marketer: 0.15 }, k: 45, suffix: ['백화점', '아울렛', '몰'] },
  fitness: { name: '헬스장', era: 5, building: 'shop', jobs: ['trainer'], k: 60, suffix: ['피트니스', '짐', '헬스'] },
  facility: { name: '시설관리', era: 5, building: 'bizbuilding', jobs: ['janitor', 'guard'], mix: { janitor: 0.55, guard: 0.45 }, k: 30, suffix: ['시설관리', '에프엠', '용역'] },
  ent: { name: '엔터테인먼트', era: 6, building: 'office', jobs: ['ent_mgr', 'singer', 'actor', 'designer'], mix: { ent_mgr: 0.3, singer: 0.3, actor: 0.3, designer: 0.1 }, k: 120, suffix: ['엔터테인먼트', '뮤직', '스타'] },
  publisher: { name: '출판사', era: 4, building: 'bizbuilding', jobs: ['editor', 'designer'], mix: { editor: 0.7, designer: 0.3 }, k: 90, suffix: ['출판', '북스', '문고'] },
  ad: { name: '광고대행사', era: 5, building: 'office', jobs: ['marketer', 'copywriter', 'designer', 'pd'], mix: { marketer: 0.35, copywriter: 0.25, designer: 0.25, pd: 0.15 }, k: 80, suffix: ['애드', '기획', '커뮤니케이션즈'] },
  florist: { name: '꽃집', era: 5, building: 'shop', jobs: ['florist'], k: 80, suffix: ['꽃집', '플라워', '화원'] },
};

// 공공 기관 (지도자가 세움). pop = 이 정도 인구가 되면 세운다
export const PUBLIC = {
  hall: { name: '관청', modern: '시청', era: 2, building: 'hall', jobs: ['official', 'soldier', 'sanitation'], mix: { official: 0.75, soldier: 0.2, sanitation: 0.2 }, per: 18 },
  academy: { name: '학당', era: 2, building: 'academy', jobs: ['scholar'], per: 40 },
  temple: { name: '신전', era: 2, building: 'temple', jobs: ['priest'], per: 25 },
  seodang: { name: '서당', era: 2, maxEra: 3, building: 'seodang', jobs: ['teacher'], per: 14, school: true },
  kinder: { name: '유치원', era: 5, building: 'kinder', jobs: ['kinder_teacher', 'nutritionist'], mix: { kinder_teacher: 0.9, nutritionist: 0.1 }, per: 14, school: true },
  school: { name: '초등학교', era: 3, building: 'school', jobs: ['teacher', 'nutritionist'], mix: { teacher: 0.9, nutritionist: 0.1 }, per: 16, school: true },
  middle: { name: '중학교', era: 4, building: 'middle', jobs: ['teacher', 'nutritionist'], mix: { teacher: 0.9, nutritionist: 0.1 }, per: 18, school: true },
  high: { name: '고등학교', era: 4, building: 'high', jobs: ['teacher', 'nutritionist'], mix: { teacher: 0.9, nutritionist: 0.1 }, per: 18, school: true },
  univ: { name: '대학교', era: 4, building: 'university', jobs: ['professor', 'researcher', 'office_clerk', 'teacher'], mix: { professor: 0.6, researcher: 0.25, office_clerk: 0.15, teacher: 0.2 }, per: 30, school: true },
  police: { name: '경찰서', era: 4, building: 'police', jobs: ['police'], per: 120, pop: 40 },
  fire: { name: '소방서', era: 4, building: 'firestation', jobs: ['firefighter', 'paramedic'], mix: { firefighter: 0.75, paramedic: 0.25 }, per: 150, pop: 45 },
  post: { name: '우체국', era: 4, building: 'postoffice', jobs: ['postman'], per: 200, pop: 50 },
  hospital: { name: '종합병원', era: 4, building: 'hospital', jobs: ['doctor', 'nurse', 'nurse_aide', 'radiologist', 'labtech', 'physio', 'pharmacist', 'office_clerk'], mix: { doctor: 0.18, nurse: 0.38, nurse_aide: 0.14, radiologist: 0.06, labtech: 0.06, physio: 0.06, pharmacist: 0.04, office_clerk: 0.08 }, per: 60, pop: 60 },
  station: { name: '기차역', era: 4, building: 'station', jobs: ['stationmaster', 'train_driver'], mix: { stationmaster: 0.5, train_driver: 0.5 }, per: 250, pop: 70 },
  power: { name: '발전소', era: 5, building: 'powerplant', jobs: ['operator', 'engineer'], per: 200, pop: 80 },
  lab: { name: '국립 연구소', era: 5, building: 'lab', jobs: ['scientist', 'researcher'], mix: { scientist: 0.5, researcher: 0.5 }, per: 25 },
  // 민간 버스 회사가 없는 작은 도시는 관청이 공영버스를 굴린다 (군내버스·마을버스)
  transit: { name: '공영버스', era: 5, building: 'busdepot', jobs: ['bus_driver', 'mechanic'], mix: { bus_driver: 0.85, mechanic: 0.15 }, per: 300, pop: 80 },
};

// 건물. cells = 차지하는 칸(20m 격자), size = [가로, 세로, 높이] m
// work = 짓는 데 드는 작업량(사람×시간). 예: 아파트 24000 → 10명이 하루 8시간씩 약 1년
// jobs = 이 건물에서 일할 수 있는 최대 인원, cap = 사는 사람 수
// ── 도로 등급과 관청 부서 ──
export const ROAD_GRADES = [
  { name: '흙길', era: 0, work: 0, verb: '' },
  { name: '자갈길', era: 2, work: 520, verb: '자갈 깔기' },
  { name: '돌길', era: 3, work: 1300, verb: '돌길 포장' },
  { name: '포장도로', era: 4, work: 2600, verb: '아스팔트 포장' },
  { name: '대로', era: 5, work: 4200, verb: '대로 확장' },
];
export const maxRoadGrade = era => (era >= 5 ? 4 : era >= 4 ? 3 : era >= 3 ? 2 : era >= 2 ? 1 : 0);

/** 관청(시청)의 부서: 시대와 공무원 수에 따라 생긴다 */
export const DEPTS = [
  { key: 'general', name: '총무과', era: 2, staff: 1, does: '행정·문서' },
  { key: 'urban', name: '도시환경과', era: 2, staff: 3, does: '도로 정비·가로수' },
  { key: 'welfare', name: '복지과', era: 4, staff: 5, does: '가난한 이웃 지원' },
  { key: 'traffic', name: '교통과', era: 5, staff: 5, does: '대로·신호등·횡단보도·버스 정류장' },
];

export const deptName = key => DEPTS.find(d => d.key === key)?.name || '';

const B = (name, era, cells, size, work, kind, color, roof, extra = {}) => ({ name, era, cells, size, work, kind, color, roof, ...extra });
export const BUILDINGS = {
  campfire: B('모닥불', 0, [1, 1], [4, 4, 1.2], 4, 'civic', '#c96a2b', 'fire'),
  hut: B('움집', 0, [1, 1], [7, 7, 3.2], 40, 'home', '#8d6b48', 'cone', { cap: 5, cost: 0 }),
  cottage: B('초가집', 1, [1, 1], [9, 8, 3.6], 240, 'home', '#cdb38a', 'gable', { roofColor: '#b9974f', cap: 6, cost: 20 }),
  house: B('기와집', 2, [1, 1], [11, 9, 4.2], 1100, 'home', '#e8e0cf', 'gable', { roofColor: '#4a5560', cap: 6, cost: 80 }),
  brickhouse: B('벽돌집', 3, [1, 1], [11, 10, 8], 2200, 'home', '#9c5a45', 'gable', { roofColor: '#5b3a2e', cap: 7, cost: 180 }),
  townhouse: B('연립주택', 4, [1, 1], [14, 12, 12], 6000, 'home', '#c9b9a6', 'flat', { cap: 12, cost: 500 }),
  apartment: B('아파트', 5, [2, 2], [30, 22, 42], 24000, 'home', '#dfe3e8', 'flat', { cap: 40, cost: 2500 }),
  skytower: B('초고층 주상복합', 6, [2, 2], [32, 32, 140], 120000, 'home', '#9fc3dc', 'flat', { glass: true, cap: 140, cost: 12000 }),
  roadwork: B('도로 공사', 0, [0, 0], [10, 10, 0], 0, 'infra', '#777777', 'none', { cost: 0 }),
  signal: B('신호등 공사', 5, [0, 0], [6, 6, 0], 368, 'infra', '#777777', 'none', { cost: 0 }),
  busstop: B('버스 정류장 공사', 4, [0, 0], [6, 6, 0], 32, 'infra', '#777777', 'none', { cost: 0 }),
  farm: B('농장', 1, [2, 2], [34, 34, 0.4], 120, 'farm', '#9bb547', 'field', { jobs: 12, cost: 15 }),
  granary: B('곡물 창고', 1, [1, 1], [10, 10, 6], 300, 'civic', '#b89a68', 'cone', { roofColor: '#8a6d3e', jobs: 4, cost: 0 }),
  lumberyard: B('목재소', 1, [2, 1], [30, 14, 6], 600, 'work', '#9b7653', 'gable', { roofColor: '#5d4430', jobs: 30, cost: 30 }),
  kiln: B('벽돌 가마', 1, [1, 1], [10, 9, 4.5], 300, 'work', '#9a5a3c', 'dome', { roofColor: '#6b3a24', jobs: 8, cost: 25 }),
  workshop: B('작업장', 1, [1, 1], [12, 10, 5], 500, 'work', '#a68660', 'gable', { roofColor: '#6b4f33', jobs: 12, cost: 40 }),
  restaurant: B('식당', 2, [1, 1], [13, 11, 5], 900, 'work', '#e8c28f', 'gable', { roofColor: '#8b3a2a', accent: '#e55', jobs: 15, cost: 90 }),
  market: B('시장', 2, [1, 1], [16, 14, 4.5], 1500, 'work', '#d9a35c', 'gable', { roofColor: '#a3402c', jobs: 20, cost: 120 }),
  tailorshop: B('옷가게', 2, [1, 1], [10, 9, 5], 800, 'work', '#d9c2e0', 'gable', { roofColor: '#6b4c7a', jobs: 8, cost: 80 }),
  forge: B('대장간', 2, [1, 1], [11, 10, 5], 1000, 'work', '#5b5550', 'gable', { roofColor: '#33302d', jobs: 8, cost: 110 }),
  mine: B('광산', 2, [1, 1], [14, 12, 8], 3000, 'work', '#6e665d', 'gable', { roofColor: '#3b3530', accent: '#111', jobs: 120, cost: 300 }),
  dock: B('선착장', 2, [2, 1], [30, 12, 4], 1500, 'work', '#8b7355', 'flat', { jobs: 40, cost: 150, nearWater: true }),
  hall: B('관청', 2, [2, 1], [30, 16, 10], 5000, 'civic', '#efe6d2', 'gable', { roofColor: '#2f4c63', jobs: 40, cost: 0 }),
  academy: B('학당', 2, [1, 1], [16, 12, 7], 2500, 'civic', '#ece2c6', 'gable', { roofColor: '#3d5a40', jobs: 20, cost: 0 }),
  temple: B('신전', 2, [1, 1], [16, 16, 12], 4000, 'civic', '#f2efe6', 'pyramid', { roofColor: '#c8a04a', jobs: 10, cost: 0 }),
  seodang: B('서당', 2, [1, 1], [12, 10, 5], 1200, 'civic', '#dccca4', 'gable', { roofColor: '#3d3832', jobs: 6, cost: 0 }),
  bakery: B('빵집', 3, [1, 1], [10, 9, 6], 1000, 'work', '#f1d9b0', 'gable', { roofColor: '#9c5b3b', jobs: 8, cost: 120 }),
  clinic: B('의원', 3, [1, 1], [16, 14, 10], 3500, 'work', '#f5f5f5', 'flat', { accent: '#d33', jobs: 20, cost: 300 }),
  school: B('초등학교', 3, [2, 1], [32, 14, 11], 7000, 'civic', '#e6c79a', 'gable', { roofColor: '#7a3b2b', jobs: 40, cost: 0 }),
  studio: B('화실', 3, [1, 1], [12, 10, 7], 1000, 'work', '#c7a7d4', 'gable', { roofColor: '#5e4370', jobs: 8, cost: 200 }),
  middle: B('중학교', 4, [2, 1], [34, 14, 13], 9000, 'civic', '#d9c4a2', 'flat', { jobs: 50, cost: 0 }),
  high: B('고등학교', 4, [2, 1], [36, 15, 15], 10000, 'civic', '#c7b39b', 'flat', { jobs: 60, cost: 0 }),
  university: B('대학교', 4, [2, 2], [40, 30, 20], 30000, 'civic', '#b9a58d', 'dome', { jobs: 200, cost: 0 }),
  bank: B('은행', 4, [1, 1], [16, 14, 16], 6000, 'work', '#d8d2c2', 'flat', { jobs: 40, cost: 900 }),
  factory: B('공장', 4, [2, 2], [36, 30, 14], 15000, 'work', '#8a8f94', 'saw', { chimney: true, jobs: 150, cost: 1500 }),
  steelmill: B('제철소', 4, [3, 3], [56, 52, 30], 60000, 'work', '#7a6f6a', 'saw', { chimney: true, jobs: 600, cost: 8000 }),
  shipyard: B('조선소', 4, [3, 3], [58, 56, 24], 70000, 'work', '#7d8a96', 'saw', { jobs: 800, cost: 9000, nearWater: 'sea' }),
  heavyplant: B('중공업 공장', 4, [3, 2], [56, 36, 20], 40000, 'work', '#8e959b', 'saw', { chimney: true, jobs: 400, cost: 6000 }),
  warehouse: B('물류 창고', 4, [2, 2], [36, 30, 10], 9000, 'work', '#b7aa98', 'saw', { jobs: 100, cost: 1200 }),
  hotel: B('호텔', 4, [2, 1], [32, 16, 36], 30000, 'work', '#e9dccb', 'flat', { jobs: 80, cost: 4000 }),
  salon: B('미용실', 4, [1, 1], [9, 9, 6], 900, 'work', '#f4c2d7', 'flat', { jobs: 6, cost: 200 }),
  police: B('경찰서', 4, [1, 1], [16, 14, 12], 6000, 'civic', '#c9d6e8', 'flat', { accent: '#1d4ed8', jobs: 40, cost: 0 }),
  firestation: B('소방서', 4, [1, 1], [16, 14, 10], 5000, 'civic', '#e9c4bd', 'flat', { accent: '#d00', jobs: 30, cost: 0 }),
  postoffice: B('우체국', 4, [1, 1], [14, 12, 9], 4000, 'civic', '#f2e0c4', 'flat', { accent: '#e85d04', jobs: 25, cost: 0 }),
  hospital: B('종합병원', 4, [2, 2], [36, 30, 30], 30000, 'civic', '#f7f7f7', 'flat', { accent: '#d33', jobs: 300, cost: 0 }),
  station: B('기차역', 4, [2, 1], [34, 14, 10], 7000, 'civic', '#d6c3a0', 'gable', { roofColor: '#3c5a74', jobs: 40, cost: 0 }),
  kinder: B('유치원', 5, [1, 1], [16, 13, 8], 3000, 'civic', '#ffe0a8', 'flat', { accent: '#ff8fab', jobs: 15, cost: 0 }),
  office: B('오피스 빌딩', 5, [1, 1], [18, 18, 55], 25000, 'work', '#7fa6c4', 'flat', { glass: true, jobs: 300, cost: 3000 }),
  carplant: B('자동차 공장', 5, [3, 3], [58, 52, 18], 60000, 'work', '#c9ced3', 'flat', { jobs: 600, cost: 9000 }),
  chemplant: B('화학 공장', 5, [2, 2], [36, 34, 22], 35000, 'work', '#b9c0b5', 'dome', { roofColor: '#9aa39a', chimney: true, jobs: 300, cost: 6000 }),
  supermarket: B('슈퍼마켓', 5, [2, 1], [34, 18, 7], 8000, 'work', '#f0f0f0', 'flat', { accent: '#e63946', jobs: 40, cost: 1500 }),
  cafe: B('카페', 5, [1, 1], [10, 10, 6], 1500, 'work', '#d8c3a5', 'flat', { accent: '#2a9d8f', jobs: 8, cost: 300 }),
  lab: B('연구소', 5, [2, 1], [32, 16, 18], 15000, 'civic', '#e9eef2', 'dome', { jobs: 80, cost: 0 }),
  stadium: B('경기장', 5, [3, 3], [56, 56, 16], 40000, 'work', '#c8ccd0', 'bowl', { jobs: 60, cost: 5000 }),
  powerplant: B('발전소', 5, [2, 2], [36, 34, 26], 30000, 'civic', '#a8b0b6', 'dome', { roofColor: '#8d969c', chimney: true, jobs: 60, cost: 0 }),
  electronics: B('전자 공장', 6, [2, 2], [38, 34, 20], 45000, 'work', '#e6ebef', 'flat', { glass: true, jobs: 500, cost: 8000 }),
  spacecenter: B('우주 센터', 7, [3, 3], [50, 50, 30], 80000, 'work', '#eef2f5', 'rocket', { jobs: 400, cost: 30000 }),
  // 현대 산업용 건물
  pharmacy: B('약국', 4, [1, 1], [10, 9, 6], 1200, 'work', '#eef6ee', 'flat', { accent: '#2e7d32', jobs: 8, cost: 150 }),
  shop: B('상가', 4, [1, 1], [13, 11, 9], 2200, 'work', '#d8d2c8', 'flat', { jobs: 14, cost: 260 }),
  bizbuilding: B('사무 빌딩', 4, [1, 1], [16, 14, 20], 8000, 'work', '#bcc4cb', 'flat', { jobs: 90, cost: 900 }),
  carehome: B('요양원', 5, [1, 1], [18, 14, 12], 6000, 'work', '#f1e8d6', 'flat', { accent: '#7cb342', jobs: 60, cost: 800 }),
  garage: B('정비소·차고', 5, [1, 1], [16, 14, 7], 2400, 'work', '#8d969e', 'flat', { accent: '#f39c12', jobs: 20, cost: 320 }),
  busdepot: B('버스 차고지', 5, [2, 1], [36, 16, 7], 5000, 'work', '#9aa1a8', 'flat', { accent: '#27ae60', jobs: 120, cost: 700 }),
  airport: B('공항', 5, [3, 3], [56, 52, 16], 60000, 'work', '#dfe6ec', 'saw', { glass: true, jobs: 900, cost: 8000 }),
  cvs: B('편의점', 6, [1, 1], [9, 8, 4], 700, 'work', '#f4f6f6', 'flat', { accent: '#16a085', jobs: 6, cost: 120 }),
  mall: B('백화점', 5, [2, 2], [38, 36, 26], 30000, 'work', '#e3dccf', 'flat', { accent: '#c0392b', jobs: 400, cost: 4000 }),
};

// 시대별 주택 양식
export const HOME_BY_ERA = ['hut', 'cottage', 'house', 'brickhouse', 'townhouse', 'apartment', 'skytower', 'skytower'];

// 정착지 등급
export const SETTLEMENT_LEVELS = [
  { name: '야영지', minB: 0 },
  { name: '마을', minB: 4 },
  { name: '읍', minB: 12, minEra: 2 },
  { name: '도시', minB: 25, minEra: 3 },
  { name: '대도시', minB: 50, minEra: 5 },
  { name: '메가시티', minB: 90, minEra: 6 },
];

// 인생 목표 (가치관에서 생겨난다)
export const GOALS = {
  wealth: { text: '큰 부자가 되기', icon: '💰' },
  company: { text: '내 회사를 세우기', icon: '🏢' },
  power: { text: '나라의 지도자가 되기', icon: '👑' },
  knowledge: { text: '세상을 바꿀 발견을 하기', icon: '🔭' },
  family: { text: '행복한 가정을 꾸리기', icon: '👨‍👩‍👧' },
  art: { text: '모두가 기억할 걸작을 남기기', icon: '🎨' },
  adventure: { text: '새로운 땅을 개척하기', icon: '🧭' },
  sport: { text: '최고의 운동선수가 되기', icon: '🏅' },
  community: { text: '모두에게 사랑받는 사람이 되기', icon: '🤝' },
  mastery: { text: '내 분야 최고의 장인이 되기', icon: '🛠️' },
};

export const RANKS = ['견습', '숙련', '베테랑', '책임자'];

export const RELATION_NAMES = {
  stranger: '모르는 사이', acquaintance: '아는 사이', friend: '친구', best: '절친', rival: '라이벌', enemy: '원수',
  crush: '짝사랑', partner: '연인', spouse: '배우자', ex: '전 연인', parent: '부모', child: '자녀', sibling: '형제자매',
};

// 학교 과정: 나이에 맞는 학교가 마을에 있어야 다닐 수 있다
export const SCHOOLS = [
  { key: 'kinder', name: '유치원', ages: [4, 7], level: 0, orgs: ['kinder'] },
  { key: 'elem', name: '초등학교', ages: [7, 13], level: 1, orgs: ['school', 'seodang'] },
  { key: 'middle', name: '중학교', ages: [13, 16], level: 2, orgs: ['middle', 'seodang'] },
  { key: 'high', name: '고등학교', ages: [16, 19], level: 3, orgs: ['high'] },
  { key: 'univ', name: '대학교', ages: [19, 23], level: 4, orgs: ['univ'], optional: true },
];
export const EDU_NAMES = ['배운 적 없음', '기초 교육', '중학교 졸업', '고등학교 졸업', '대학교 졸업'];
/** 시대가 허락하는 최고 학력 */
export const maxEduForEra = era => (era <= 1 ? 0 : era <= 3 ? 1 : 4);
