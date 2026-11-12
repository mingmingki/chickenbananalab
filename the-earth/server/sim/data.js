// 게임 콘텐츠 표: 시대, 기술 발견, 직업, 산업(회사), 건물, 인생 목표

export const SKILLS = {
  farming: '농사·채집', construction: '건축', crafting: '제작', science: '과학', commerce: '상업',
  leadership: '리더십', medicine: '의술', art: '예술', teaching: '교육', athletics: '운동',
};

// 시대: tech = 이 시대에 들어서기 위한 누적 기술력
export const ERAS = [
  { id: 0, name: '원시 시대', tech: 0, leader: '족장', prod: 1.0, mort: 2.0, wage: 4, cityK: 1.0 },
  { id: 1, name: '농경 시대', tech: 600, leader: '촌장', prod: 1.35, mort: 1.7, wage: 6, cityK: 1.2 },
  { id: 2, name: '고대 문명', tech: 4000, leader: '왕', prod: 1.8, mort: 1.45, wage: 10, cityK: 1.5 },
  { id: 3, name: '중세', tech: 15000, leader: '국왕', prod: 2.4, mort: 1.3, wage: 16, cityK: 1.8 },
  { id: 4, name: '산업 혁명', tech: 45000, leader: '총리', prod: 3.5, mort: 1.1, wage: 30, cityK: 2.3 },
  { id: 5, name: '근대', tech: 120000, leader: '대통령', prod: 5, mort: 0.9, wage: 55, cityK: 3 },
  { id: 6, name: '정보화 시대', tech: 280000, leader: '대통령', prod: 7, mort: 0.75, wage: 90, cityK: 4 },
  { id: 7, name: '우주 시대', tech: 620000, leader: '대통령', prod: 10, mort: 0.6, wage: 140, cityK: 5 },
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

// 직업. self = 고용주 없이 스스로 하는 일, pub = 나라가 고용
// out: food(식량), tech(기술), build(건설), money(매출), art, heal
export const JOBS = {
  gatherer: { name: '채집꾼', era: 0, maxEra: 1, skill: 'farming', self: true, out: 'food', rate: 2.2, fit: { ath: .3, C: .3 } },
  hunter: { name: '사냥꾼', era: 0, maxEra: 1, skill: 'athletics', self: true, out: 'food', rate: 2.6, fit: { ath: .7, N: -.2 } },
  toolmaker: { name: '도구 장인', era: 0, maxEra: 1, skill: 'crafting', self: true, out: 'tech', rate: 0.9, fit: { int: .3, cre: .4, C: .2 } },
  storyteller: { name: '이야기꾼', era: 0, maxEra: 2, skill: 'art', self: true, out: 'art', rate: 1, fit: { cre: .5, E: .4, cha: .3 } },
  shaman: { name: '주술사', era: 0, maxEra: 2, skill: 'medicine', self: true, out: 'heal', rate: 1, fit: { int: .3, O: .4, cha: .2 } },
  builder: { name: '목수', era: 1, skill: 'construction', ind: 'construction', out: 'build', rate: 1, fit: { ath: .4, C: .4 } },
  farmer: { name: '농부', era: 1, skill: 'farming', ind: 'farm', out: 'food', rate: 4, fit: { C: .4, ath: .3, A: .1 } },
  craftsman: { name: '장인', era: 1, skill: 'crafting', ind: 'workshop', out: 'money', rate: 1, fit: { cre: .4, C: .4 } },
  merchant: { name: '상인', era: 2, skill: 'commerce', ind: 'trade', out: 'money', rate: 1.2, fit: { E: .5, cha: .5, int: .2 } },
  blacksmith: { name: '대장장이', era: 2, skill: 'crafting', ind: 'smithy', out: 'money', rate: 1.1, fit: { ath: .5, C: .3 } },
  scholar: { name: '학자', era: 2, skill: 'science', pub: 'academy', out: 'tech', rate: 2, fit: { int: .7, O: .5 } },
  priest: { name: '사제', era: 2, maxEra: 4, skill: 'leadership', pub: 'temple', out: 'heal', rate: 1, fit: { A: .4, cha: .3, C: .2 } },
  official: { name: '관리', era: 2, skill: 'leadership', pub: 'hall', out: 'gov', rate: 1, fit: { C: .5, int: .3, cha: .2 } },
  soldier: { name: '병사', era: 2, maxEra: 5, skill: 'athletics', pub: 'hall', out: 'gov', rate: 1, fit: { ath: .7, C: .2, A: -.2 } },
  doctor: { name: '의사', era: 3, skill: 'medicine', ind: 'clinic', out: 'heal', rate: 1.4, fit: { int: .5, A: .4, C: .3 } },
  teacher: { name: '교사', era: 3, skill: 'teaching', pub: 'school', out: 'teach', rate: 1, fit: { A: .4, E: .3, int: .3 } },
  artist: { name: '예술가', era: 3, skill: 'art', ind: 'studio', out: 'art', rate: 1.2, fit: { cre: .7, O: .5 } },
  banker: { name: '은행원', era: 4, skill: 'commerce', ind: 'bank', out: 'money', rate: 1.6, fit: { int: .4, C: .5 } },
  worker: { name: '공장 노동자', era: 4, skill: 'crafting', ind: 'factory', out: 'money', rate: 1.5, fit: { C: .4, ath: .3 } },
  engineer: { name: '엔지니어', era: 4, skill: 'science', ind: 'factory', out: 'tech', rate: 1.5, fit: { int: .6, C: .3 } },
  scientist: { name: '과학자', era: 5, skill: 'science', pub: 'lab', out: 'tech', rate: 3, fit: { int: .8, O: .5 } },
  journalist: { name: '기자', era: 5, skill: 'art', ind: 'media', out: 'money', rate: 1.6, fit: { E: .4, O: .4, int: .2 } },
  athlete: { name: '운동선수', era: 5, skill: 'athletics', ind: 'sports', out: 'money', rate: 2, fit: { ath: 1, E: .2 } },
  programmer: { name: '프로그래머', era: 6, skill: 'science', ind: 'tech', out: 'tech', rate: 2.2, fit: { int: .7, C: .3, E: -.1 } },
  designer: { name: '디자이너', era: 6, skill: 'art', ind: 'tech', out: 'money', rate: 2, fit: { cre: .7, O: .4 } },
  astronaut: { name: '우주비행사', era: 7, skill: 'athletics', ind: 'aerospace', out: 'tech', rate: 3, fit: { ath: .6, int: .5, N: -.4 } },
  rocketeer: { name: '로켓 엔지니어', era: 7, skill: 'science', ind: 'aerospace', out: 'tech', rate: 3.5, fit: { int: .8, C: .4 } },
};

// 민간 산업(회사). k = 종사자 1명이 감당하는 인구 (수요), price = 시간당 매출 단가 배수
export const INDUSTRIES = {
  farm: { name: '농장', era: 1, building: 'farm', jobs: ['farmer'], k: 5, suffix: ['농장', '농원', '영농'] },
  construction: { name: '건설', era: 1, building: 'workshop', jobs: ['builder'], k: 7, suffix: ['건설', '건축', '목공소'] },
  workshop: { name: '공방', era: 1, building: 'workshop', jobs: ['craftsman'], k: 8, suffix: ['공방', '공예', '도자기'] },
  trade: { name: '상회', era: 2, building: 'market', jobs: ['merchant'], k: 9, suffix: ['상회', '상점', '무역'] },
  smithy: { name: '대장간', era: 2, building: 'forge', jobs: ['blacksmith'], k: 14, suffix: ['대장간', '철공소', '제철'] },
  clinic: { name: '병원', era: 3, building: 'clinic', jobs: ['doctor'], k: 18, suffix: ['의원', '병원', '메디컬'] },
  studio: { name: '예술 공방', era: 3, building: 'studio', jobs: ['artist'], k: 22, suffix: ['화방', '스튜디오', '아틀리에'] },
  bank: { name: '은행', era: 4, building: 'bank', jobs: ['banker'], k: 30, suffix: ['은행', '금융', '증권'] },
  factory: { name: '제조업', era: 4, building: 'factory', jobs: ['worker', 'engineer'], k: 6, suffix: ['공업', '제작소', '중공업'] },
  media: { name: '언론사', era: 5, building: 'office', jobs: ['journalist'], k: 40, suffix: ['신문', '일보', '방송'] },
  sports: { name: '스포츠 구단', era: 5, building: 'stadium', jobs: ['athlete'], k: 45, suffix: ['FC', '스포츠', '구단'] },
  tech: { name: 'IT 기업', era: 6, building: 'office', jobs: ['programmer', 'designer'], k: 14, suffix: ['테크', '소프트', '랩스'] },
  aerospace: { name: '우주항공', era: 7, building: 'spacecenter', jobs: ['astronaut', 'rocketeer'], k: 40, suffix: ['우주항공', '스페이스', '로켓'] },
};

// 공공 기관 (지도자가 세움)
export const PUBLIC = {
  hall: { name: '관청', era: 2, building: 'hall', jobs: ['official', 'soldier'], per: 18 },
  academy: { name: '학당', era: 2, building: 'academy', jobs: ['scholar'], per: 10 },
  temple: { name: '신전', era: 2, building: 'temple', jobs: ['priest'], per: 25 },
  school: { name: '학교', era: 3, building: 'school', jobs: ['teacher'], per: 12 },
  lab: { name: '국립 연구소', era: 5, building: 'lab', jobs: ['scientist'], per: 10 },
};

// 건물. cells = 차지하는 칸(20m 격자), size = [가로, 세로, 높이] m, work = 건설에 필요한 작업 시간
// kind: home / civic / work / farm
export const BUILDINGS = {
  campfire: { name: '모닥불', era: 0, cells: [1, 1], size: [4, 4, 1.2], work: 3, kind: 'civic', color: '#c96a2b', roof: 'fire' },
  hut: { name: '움집', era: 0, cells: [1, 1], size: [7, 7, 3.2], work: 14, kind: 'home', cap: 5, color: '#8d6b48', roof: 'cone', cost: 0 },
  cottage: { name: '초가집', era: 1, cells: [1, 1], size: [9, 8, 3.6], work: 30, kind: 'home', cap: 6, color: '#cdb38a', roof: 'gable', roofColor: '#b9974f', cost: 20 },
  house: { name: '기와집', era: 2, cells: [1, 1], size: [11, 9, 4.2], work: 55, kind: 'home', cap: 6, color: '#e8e0cf', roof: 'gable', roofColor: '#4a5560', cost: 80 },
  brickhouse: { name: '벽돌집', era: 3, cells: [1, 1], size: [11, 10, 8], work: 90, kind: 'home', cap: 7, color: '#9c5a45', roof: 'gable', roofColor: '#5b3a2e', cost: 180 },
  townhouse: { name: '연립주택', era: 4, cells: [1, 1], size: [14, 12, 12], work: 160, kind: 'home', cap: 12, color: '#c9b9a6', roof: 'flat', cost: 500 },
  apartment: { name: '아파트', era: 5, cells: [2, 2], size: [30, 22, 42], work: 700, kind: 'home', cap: 40, color: '#dfe3e8', roof: 'flat', cost: 2500 },
  skytower: { name: '초고층 주상복합', era: 6, cells: [2, 2], size: [32, 32, 140], work: 2400, kind: 'home', cap: 140, color: '#9fc3dc', roof: 'flat', glass: true, cost: 12000 },
  farm: { name: '농장', era: 1, cells: [2, 2], size: [34, 34, 0.4], work: 12, kind: 'farm', color: '#9bb547', roof: 'field', cost: 15 },
  workshop: { name: '작업장', era: 1, cells: [1, 1], size: [12, 10, 5], work: 40, kind: 'work', color: '#a68660', roof: 'gable', roofColor: '#6b4f33', cost: 40 },
  granary: { name: '곡물 창고', era: 1, cells: [1, 1], size: [10, 10, 6], work: 35, kind: 'civic', color: '#b89a68', roof: 'cone', roofColor: '#8a6d3e', cost: 0 },
  market: { name: '시장', era: 2, cells: [1, 1], size: [16, 14, 4.5], work: 60, kind: 'work', color: '#d9a35c', roof: 'gable', roofColor: '#a3402c', cost: 120 },
  forge: { name: '대장간', era: 2, cells: [1, 1], size: [11, 10, 5], work: 55, kind: 'work', color: '#5b5550', roof: 'gable', roofColor: '#33302d', cost: 110 },
  hall: { name: '관청', era: 2, cells: [2, 1], size: [30, 16, 10], work: 160, kind: 'civic', color: '#efe6d2', roof: 'gable', roofColor: '#2f4c63', cost: 0 },
  academy: { name: '학당', era: 2, cells: [1, 1], size: [16, 12, 7], work: 90, kind: 'civic', color: '#ece2c6', roof: 'gable', roofColor: '#3d5a40', cost: 0 },
  temple: { name: '신전', era: 2, cells: [1, 1], size: [16, 16, 12], work: 140, kind: 'civic', color: '#f2efe6', roof: 'pyramid', roofColor: '#c8a04a', cost: 0 },
  clinic: { name: '병원', era: 3, cells: [1, 1], size: [16, 14, 10], work: 120, kind: 'work', color: '#f5f5f5', roof: 'flat', accent: '#d33', cost: 300 },
  school: { name: '학교', era: 3, cells: [2, 1], size: [32, 14, 11], work: 150, kind: 'civic', color: '#e6c79a', roof: 'gable', roofColor: '#7a3b2b', cost: 0 },
  studio: { name: '화실', era: 3, cells: [1, 1], size: [12, 10, 7], work: 70, kind: 'work', color: '#c7a7d4', roof: 'gable', roofColor: '#5e4370', cost: 200 },
  bank: { name: '은행', era: 4, cells: [1, 1], size: [16, 14, 16], work: 220, kind: 'work', color: '#d8d2c2', roof: 'flat', cost: 900 },
  factory: { name: '공장', era: 4, cells: [2, 2], size: [36, 30, 14], work: 400, kind: 'work', color: '#8a8f94', roof: 'saw', chimney: true, cost: 1500 },
  office: { name: '오피스 빌딩', era: 5, cells: [1, 1], size: [18, 18, 55], work: 650, kind: 'work', color: '#7fa6c4', roof: 'flat', glass: true, cost: 3000 },
  lab: { name: '연구소', era: 5, cells: [2, 1], size: [32, 16, 18], work: 500, kind: 'civic', color: '#e9eef2', roof: 'dome', cost: 0 },
  stadium: { name: '경기장', era: 5, cells: [3, 3], size: [56, 56, 16], work: 900, kind: 'work', color: '#c8ccd0', roof: 'bowl', cost: 5000 },
  spacecenter: { name: '우주 센터', era: 7, cells: [3, 3], size: [50, 50, 30], work: 2500, kind: 'work', color: '#eef2f5', roof: 'rocket', cost: 30000 },
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
