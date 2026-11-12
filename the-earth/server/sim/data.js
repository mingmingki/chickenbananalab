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
  official: J('공무원', 2, 'leadership', 'gov', 1, { C: .5, int: .3, cha: .2 }, { edu: 2, shift: 'gov' }),
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
};

// 민간 산업(회사). k = 종사자 1명이 감당하는 인구(수요). needs = 지형 조건, requires = 나라에 먼저 있어야 하는 산업
export const INDUSTRIES = {
  farm: { name: '농장', era: 1, building: 'farm', jobs: ['farmer'], k: 5, suffix: ['농장', '농원', '영농'] },
  lumber: { name: '목재소', era: 1, building: 'lumberyard', jobs: ['lumberjack'], k: 30, needs: 'forest', suffix: ['목재', '제재소', '임업'] },
  brickworks: { name: '벽돌·기와', era: 1, building: 'kiln', jobs: ['brickmaker'], k: 24, suffix: ['벽돌', '기와', '요업', '건자재'] },
  construction: { name: '건설', era: 1, building: 'workshop', jobs: ['builder'], k: 7, suffix: ['건설', '건축', '종합건설'] },
  workshop: { name: '공방', era: 1, building: 'workshop', jobs: ['craftsman'], k: 10, suffix: ['공방', '공예', '도자기'] },
  restaurant: { name: '식당', era: 2, building: 'restaurant', jobs: ['cook', 'server'], k: 14, suffix: ['식당', '밥집', '국밥', '반점'] },
  trade: { name: '상회', era: 2, building: 'market', jobs: ['merchant'], k: 12, suffix: ['상회', '상점', '무역'] },
  tailor: { name: '옷가게', era: 2, building: 'tailorshop', jobs: ['tailor'], k: 28, suffix: ['양복점', '옷가게', '의류'] },
  smithy: { name: '대장간', era: 2, building: 'forge', jobs: ['blacksmith'], k: 18, suffix: ['대장간', '철공소'] },
  mining: { name: '광산', era: 2, building: 'mine', jobs: ['miner'], k: 22, needs: 'mountain', suffix: ['광업', '탄광', '광산'] },
  fishery: { name: '수산', era: 2, building: 'dock', jobs: ['sailor'], k: 12, needs: 'water', suffix: ['수산', '선단', '어업'] },
  bakery: { name: '빵집', era: 3, building: 'bakery', jobs: ['baker'], k: 30, suffix: ['베이커리', '제과', '빵집'] },
  clinic: { name: '의원', era: 3, building: 'clinic', jobs: ['doctor', 'nurse'], k: 18, suffix: ['의원', '병원', '메디컬'] },
  studio: { name: '예술 공방', era: 3, building: 'studio', jobs: ['artist'], k: 26, suffix: ['화방', '스튜디오', '아틀리에'] },
  steel: { name: '제철', era: 4, building: 'steelmill', jobs: ['steelworker', 'engineer'], k: 18, requires: 'mining', suffix: ['제철', '철강', '스틸'] },
  shipbuilding: { name: '조선', era: 4, building: 'shipyard', jobs: ['welder', 'engineer', 'worker'], k: 16, needs: 'sea', requires: 'steel', suffix: ['조선', '중공업', '해양'] },
  machinery: { name: '중공업', era: 4, building: 'heavyplant', jobs: ['machinist', 'engineer', 'worker'], k: 16, requires: 'steel', suffix: ['중공업', '기계', '엔진'] },
  factory: { name: '경공업', era: 4, building: 'factory', jobs: ['worker', 'engineer'], k: 8, suffix: ['공업', '방직', '제작소'] },
  bank: { name: '은행', era: 4, building: 'bank', jobs: ['banker'], k: 35, suffix: ['은행', '금융', '증권'] },
  logistics: { name: '운수·물류', era: 4, building: 'warehouse', jobs: ['driver', 'worker'], k: 22, suffix: ['운수', '통운', '물류', '택배'] },
  hotel: { name: '호텔', era: 4, building: 'hotel', jobs: ['hotelier', 'cook', 'server'], k: 45, needs: 'scenic', suffix: ['호텔', '리조트', '여관'] },
  salon: { name: '미용실', era: 4, building: 'salon', jobs: ['hairdresser'], k: 40, suffix: ['헤어', '미용실', '살롱'] },
  auto: { name: '자동차', era: 5, building: 'carplant', jobs: ['assembler', 'engineer', 'designer'], k: 16, requires: 'steel', suffix: ['자동차', '모터스', '오토'] },
  chemical: { name: '화학', era: 5, building: 'chemplant', jobs: ['chemist', 'worker'], k: 30, suffix: ['화학', '케미칼', '정유'] },
  retail: { name: '슈퍼마켓', era: 5, building: 'supermarket', jobs: ['clerk'], k: 22, suffix: ['마트', '슈퍼', '상사'] },
  cafe: { name: '카페', era: 5, building: 'cafe', jobs: ['barista'], k: 35, suffix: ['커피', '카페', '다방'] },
  media: { name: '언론사', era: 5, building: 'office', jobs: ['journalist'], k: 45, suffix: ['신문', '일보', '방송'] },
  sports: { name: '스포츠 구단', era: 5, building: 'stadium', jobs: ['athlete'], k: 50, suffix: ['FC', '스포츠', '구단'] },
  electronics: { name: '전자', era: 6, building: 'electronics', jobs: ['assembler', 'engineer', 'programmer'], k: 14, requires: 'chemical', suffix: ['전자', '반도체', '일렉트릭'] },
  tech: { name: 'IT 기업', era: 6, building: 'office', jobs: ['programmer', 'designer'], k: 16, suffix: ['테크', '소프트', '랩스'] },
  aerospace: { name: '우주항공', era: 7, building: 'spacecenter', jobs: ['astronaut', 'rocketeer', 'engineer'], k: 40, requires: 'electronics', suffix: ['우주항공', '스페이스', '로켓'] },
};

// 공공 기관 (지도자가 세움). pop = 이 정도 인구가 되면 세운다
export const PUBLIC = {
  hall: { name: '관청', modern: '시청', era: 2, building: 'hall', jobs: ['official', 'soldier'], per: 18 },
  academy: { name: '학당', era: 2, building: 'academy', jobs: ['scholar'], per: 40 },
  temple: { name: '신전', era: 2, building: 'temple', jobs: ['priest'], per: 25 },
  seodang: { name: '서당', era: 2, maxEra: 3, building: 'seodang', jobs: ['teacher'], per: 14, school: true },
  kinder: { name: '유치원', era: 5, building: 'kinder', jobs: ['teacher'], per: 14, school: true },
  school: { name: '초등학교', era: 3, building: 'school', jobs: ['teacher'], per: 16, school: true },
  middle: { name: '중학교', era: 4, building: 'middle', jobs: ['teacher'], per: 18, school: true },
  high: { name: '고등학교', era: 4, building: 'high', jobs: ['teacher'], per: 18, school: true },
  univ: { name: '대학교', era: 4, building: 'university', jobs: ['professor', 'teacher'], per: 30, school: true },
  police: { name: '경찰서', era: 4, building: 'police', jobs: ['police'], per: 120, pop: 40 },
  fire: { name: '소방서', era: 4, building: 'firestation', jobs: ['firefighter'], per: 150, pop: 45 },
  post: { name: '우체국', era: 4, building: 'postoffice', jobs: ['postman'], per: 200, pop: 50 },
  hospital: { name: '종합병원', era: 4, building: 'hospital', jobs: ['doctor', 'nurse'], per: 60, pop: 60 },
  station: { name: '기차역', era: 4, building: 'station', jobs: ['stationmaster'], per: 250, pop: 70 },
  power: { name: '발전소', era: 5, building: 'powerplant', jobs: ['operator', 'engineer'], per: 200, pop: 80 },
  lab: { name: '국립 연구소', era: 5, building: 'lab', jobs: ['scientist'], per: 25 },
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
  { key: 'traffic', name: '교통과', era: 5, staff: 7, does: '대로·신호등·횡단보도' },
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
