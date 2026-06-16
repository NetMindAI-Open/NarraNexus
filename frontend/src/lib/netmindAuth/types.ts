/** Verified NetMind user as returned by emailLogin / userCallBack. */
export interface NetmindUser {
  userSystemCode: string;
  email: string;
  nickName?: string;
  userHeadImage?: string;
  loginToken: string;
  [key: string]: unknown;
}

/** Returned by userCallBack when a third-party account needs binding. */
export interface AuthBindInfo {
  bandType: number; // 1: needs email+code, 2: confirm third-party email, 3: bind existing
  identifyCode: string;
  thirdEmail?: string;
  canBandEmail?: string;
  canBandNick?: string;
}
