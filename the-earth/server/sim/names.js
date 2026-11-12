// 문화권별 이름, 정착지 이름, 회사 이름
import { pick } from './util.js';

const P = {
  ko: {
    sur: '김 이 박 최 정 강 조 윤 장 임 한 오 서 신 권 황 안 송 류 전 홍 고 문 양 손 배 백 허 유 남 심 노 하 곽 성 차 주 우 구 민 진 나 지 엄 원 천 방 공'.split(' '),
    M: '민준 서준 도윤 예준 시우 하준 주원 지호 지후 준우 준서 건우 현우 우진 선우 서진 민재 현준 연우 유준 정우 승우 승현 시윤 준혁 은우 지환 승민 유찬 태윤 민성 동현 성민 재윤 한결 이안 태민 진우 재현 도현 윤호 상훈 영수 철수 대현 경민 형준 기현'.split(' '),
    F: '서연 서윤 지우 서현 민서 하은 하윤 윤서 지유 지민 채원 수아 지아 다은 은서 예은 수빈 소율 예린 하린 지원 유나 시은 채은 소윤 예서 아린 나은 가은 서아 다인 하율 지안 서영 유진 민지 수민 혜원 보람 은비 미소 현주 다현 소희 예지 나연'.split(' '),
    order: 'sur-given', sep: '',
  },
  ja: {
    sur: 'Sato Suzuki Takahashi Tanaka Watanabe Ito Yamamoto Nakamura Kobayashi Kato Yoshida Yamada Sasaki Matsumoto Inoue Kimura'.split(' '),
    M: 'Haruto Sota Yuto Riku Hinata Minato Ren Yamato Kaito Sora Hiroshi Takumi Daiki Kenta Shota'.split(' '),
    F: 'Himari Yui Mei Sakura Hana Aoi Rin Yuna Mio Akari Emi Nanami Haruka Kaede Saki'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  zh: {
    sur: 'Wang Li Zhang Liu Chen Yang Huang Zhao Wu Zhou Xu Sun Ma Zhu Hu Guo'.split(' '),
    M: 'Wei Hao Jun Ming Lei Yang Jie Tao Bo Kai Peng Yu Zhen Long Feng'.split(' '),
    F: 'Mei Lin Xin Hui Jing Yan Ling Fang Xiu Na Qian Yue Ying Lan Ting'.split(' '),
    order: 'sur-given', sep: ' ',
  },
  vi: {
    sur: 'Nguyen Tran Le Pham Hoang Phan Vu Dang Bui Do Ho Ngo Duong Ly'.split(' '),
    M: 'Minh Huy Khang Bao Phuc Anh Duc Long Nam Quang Tuan Hieu Son Dung'.split(' '),
    F: 'Linh Lan Mai Thao Ngoc Huong Trang Hoa Vy Chi Thu Ha Nhung Yen'.split(' '),
    order: 'sur-given', sep: ' ',
  },
  in: {
    sur: 'Sharma Patel Singh Kumar Gupta Reddy Iyer Nair Das Rao Khan Mehta Joshi Verma'.split(' '),
    M: 'Aarav Vihaan Arjun Rohan Aditya Ishaan Kabir Rahul Vikram Dev Ravi Sanjay Amit Karan'.split(' '),
    F: 'Saanvi Ananya Diya Priya Aadhya Isha Kavya Meera Riya Tara Neha Pooja Lakshmi Anika'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  ar: {
    sur: 'Haddad Nasser Khalil Hamdan Mansour Saleh Aziz Farouk Rahman Karimi Darwish Habib Yilmaz Demir'.split(' '),
    M: 'Mohammed Ahmed Omar Ali Youssef Khalid Hassan Ibrahim Hamza Karim Tariq Samir Emre Reza'.split(' '),
    F: 'Fatima Aisha Mariam Layla Nour Sara Zainab Huda Amira Salma Yasmin Rania Elif Leila'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  ru: {
    sur: 'Ivanov Smirnov Kuznetsov Popov Volkov Sokolov Lebedev Kozlov Novikov Morozov Petrov Pavlov'.split(' '),
    M: 'Alexander Dmitry Maxim Ivan Mikhail Artem Sergei Nikita Andrei Egor Pavel Oleg'.split(' '),
    F: 'Anastasia Maria Anna Sofia Daria Polina Elena Olga Natalia Irina Vera Ksenia'.split(' '),
    order: 'given-sur', sep: ' ', femSur: s => s + 'a',
  },
  de: {
    sur: 'Müller Schmidt Schneider Fischer Weber Meyer Wagner Becker Schulz Hoffmann Jansen Larsen Nowak Berg'.split(' '),
    M: 'Ben Paul Finn Felix Jonas Elias Noah Leon Lukas Max Erik Lars Jan Anton'.split(' '),
    F: 'Emilia Mia Hannah Emma Lina Clara Lea Marie Anna Lena Freya Ida Greta Sofie'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  fr: {
    sur: 'Martin Bernard Dubois Thomas Robert Richard Petit Durand Leroy Moreau Laurent Simon'.split(' '),
    M: 'Louis Gabriel Léo Raphaël Arthur Jules Hugo Lucas Adam Nathan Théo Paul'.split(' '),
    F: 'Jade Louise Emma Alice Chloé Léa Manon Inès Camille Zoé Juliette Lucie'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  it: {
    sur: 'Rossi Russo Ferrari Esposito Bianchi Romano Colombo Ricci Marino Greco Popescu Papadopoulos'.split(' '),
    M: 'Leonardo Francesco Alessandro Lorenzo Mattia Andrea Gabriele Tommaso Marco Nikos Luka Stefan'.split(' '),
    F: 'Sofia Giulia Aurora Alice Ginevra Emma Giorgia Beatrice Chiara Francesca Elena Maria'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  es: {
    sur: 'García Rodríguez González Fernández López Martínez Sánchez Pérez Gómez Díaz Torres Ramírez'.split(' '),
    M: 'Mateo Santiago Sebastián Diego Lucas Nicolás Alejandro Daniel Martín Pablo Javier Carlos'.split(' '),
    F: 'Sofía Valentina Isabella Camila Lucía Martina María Elena Paula Daniela Carmen Ximena'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  pt: {
    sur: 'Silva Santos Oliveira Souza Pereira Costa Ferreira Almeida Carvalho Lima Ribeiro Gomes'.split(' '),
    M: 'Miguel Arthur Heitor Davi Gabriel Bernardo João Pedro Rafael Lucas Thiago Mateus'.split(' '),
    F: 'Helena Alice Laura Manuela Valentina Sophia Isabella Beatriz Ana Júlia Mariana Clara'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  af: {
    sur: 'Okafor Mensah Mwangi Diallo Adeyemi Banda Okoro Nkosi Kamau Traoré Abebe Osei'.split(' '),
    M: 'Kwame Kofi Chidi Tunde Jabari Baraka Sefu Themba Emeka Juma Tendai Yaw'.split(' '),
    F: 'Amara Zuri Nia Adaeze Imani Ayo Thandiwe Kesi Folami Makena Abena Chiamaka'.split(' '),
    order: 'given-sur', sep: ' ',
  },
  en: {
    sur: 'Smith Johnson Williams Brown Jones Miller Davis Wilson Taylor Clark Walker Hall Young King Wright Hill Scott Green Baker Carter'.split(' '),
    M: 'James Liam Noah Oliver Elijah William Henry Lucas Benjamin Theodore Jack Leo Owen Samuel Daniel Ethan'.split(' '),
    F: 'Olivia Emma Charlotte Amelia Sophia Mia Isabella Ava Evelyn Luna Harper Ella Grace Chloe Lily Nora'.split(' '),
    order: 'given-sur', sep: ' ',
  },
};

const pool = c => P[c] || P.en;

export function randomName(culture, gender, surname = null) {
  const p = pool(culture);
  let sur = surname || pick(p.sur);
  if (gender === 'F' && p.femSur && !surname) sur = p.femSur(sur);
  const given = pick(p[gender] || p.M);
  return p.order === 'sur-given' ? sur + p.sep + given : given + p.sep + sur;
}

/** 이름에서 성을 추정 (자녀에게 물려주기 위해) */
export function surnameOf(name, culture) {
  const p = pool(culture);
  if (!name) return pick(p.sur);
  const parts = name.trim().split(/\s+/);
  if (culture === 'ko' && parts.length === 1) return name.length >= 3 ? name.slice(0, 1) : pick(p.sur);
  if (parts.length === 1) return pick(p.sur);
  let s = p.order === 'sur-given' ? parts[0] : parts[parts.length - 1];
  if (p.femSur && s.endsWith('a')) s = s.slice(0, -1);
  return s;
}

export function childName(culture, gender, fatherName, motherName) {
  const sur = surnameOf(fatherName || motherName, culture);
  const p = pool(culture);
  let s = sur;
  if (gender === 'F' && p.femSur) s = p.femSur(sur);
  const given = pick(p[gender] || p.M);
  return p.order === 'sur-given' ? s + p.sep + given : given + p.sep + s;
}

const PLACE = {
  ko: { a: '한 새 솔 달 별 늘 푸른 누리 해 맑은 가람 바람 은 금 초록 아라 하늘 미르 온 다솜 새벽'.split(' '), b: '마을 골 내 터 뜰 나루 들 고을'.split(' ') },
  ja: { a: 'Aki Haru Kawa Yama Mori Hoshi Tsuki Sakura Kiri Ishi Kaze Asa'.split(' '), b: 'mura saki hara gawa zawa yama shima no'.split(' ') },
  zh: { a: 'Qing Long Bai Jin Shan Hua Ping An Yun Xi Dong Nan'.split(' '), b: 'cun zhen ling he shan zhou'.split(' ') },
  ru: { a: 'Novo Bel Krasno Zele Svet Zarech Berez Lesno Yasno Tikho'.split(' '), b: 'grad sk ovo insk ograd ye'.split(' ') },
  es: { a: 'San Villa Santa Puerto Monte Río Valle Agua Sol Nueva'.split(' '), b: ' Clara Verde Alta Hermosa Luz Blanca Real Esperanza'.split(' ') },
  pt: { a: 'São Vila Porto Monte Rio Vale Boa Nova Santa Bela'.split(' '), b: ' Vista Verde Alegre Luz Esperança Aurora'.split(' ') },
  fr: { a: 'Belle Mont Saint Val Clair Beau Vert Fontaine Ville'.split(' '), b: 'mont ville val fleur roche bois'.split(' ') },
  de: { a: 'Grün Wald Berg Stein Rosen Lind Hoch Sonnen Fels Eich'.split(' '), b: 'dorf burg feld heim tal hausen'.split(' ') },
  it: { a: 'Monte Villa Porto Bella Castel Santa Valle Fonte'.split(' '), b: 'fiore verde alto chiara luce mare'.split(' ') },
  ar: { a: 'Al-Nur Wadi Bab Ras Jabal Ain Dar Qasr'.split(' '), b: ' Salam Amal Zahra Noor Rimal Najm'.split(' ') },
  in: { a: 'Shanti Surya Chandra Ganga Raja Sundar Nava Hari'.split(' '), b: 'pur nagar abad garh puram'.split(' ') },
  vi: { a: 'Hoa Thanh An Binh Phu Tan Long Ha'.split(' '), b: ' Son Giang Lam Dien Thuy Hai'.split(' ') },
  af: { a: 'Umoja Jua Kilima Maji Nyota Amani Baraka Zawadi'.split(' '), b: 'ni ville ra wa ko ba'.split(' ') },
  en: { a: 'Oak River Stone Green Ash Elm Fair North Silver Red Wolf Bright Maple Clear'.split(' '), b: 'ton field ford wood haven dale brook ridge port stead'.split(' ') },
};

export function settlementName(culture, taken = new Set()) {
  const p = PLACE[culture] || PLACE.en;
  for (let i = 0; i < 40; i++) {
    const n = (pick(p.a) + pick(p.b)).trim();
    if (!taken.has(n)) return n;
  }
  return pick(p.a) + ' ' + Math.floor(Math.random() * 99);
}

const BRAND = '한빛 새벽 푸른 별빛 미래 누리 다온 하늘 바다 열매 온누리 초록 은하 태양 무지개 가온 나래 두레 모아 해솔 비전 새길 큰뜻 으뜸'.split(' ');

export function companyName(ind, founder, culture, taken = new Set()) {
  const suffix = pick(ind.suffix);
  for (let i = 0; i < 30; i++) {
    let n;
    const r = Math.random();
    if (r < 0.35) {
      const sur = surnameOf(founder.name, culture);
      n = culture === 'ko' ? `${sur}씨네 ${suffix}` : `${sur} ${suffix}`;
    } else n = `${pick(BRAND)}${suffix.length <= 2 ? '' : ' '}${suffix}`;
    if (!taken.has(n)) return n;
  }
  return `${pick(BRAND)} ${suffix} ${Math.floor(Math.random() * 90 + 10)}`;
}

/** 플레이어가 지은 이름에서 '이름' 부분만 떼어 가족의 성을 붙인다 */
export function givenOf(name, culture) {
  const t = String(name || '').trim();
  if (!t) return null;
  const parts = t.split(/\s+/);
  if (culture === 'ko') return parts.length > 1 ? parts[parts.length - 1] : t.length >= 3 ? t.slice(1) : t;
  const p = pool(culture);
  if (parts.length === 1) return t;
  return p.order === 'sur-given' ? parts.slice(1).join(' ') : parts[0];
}
export function familyName(culture, gender, parentName, given) {
  const p = pool(culture);
  let sur = surnameOf(parentName, culture);
  if (gender === 'F' && p.femSur) sur = p.femSur(sur);
  if (!given) given = pick(p[gender] || p.M);
  return p.order === 'sur-given' ? sur + p.sep + given : given + p.sep + sur;
}
