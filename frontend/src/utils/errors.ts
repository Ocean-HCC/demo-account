import { ApiError } from '../api/client';

export const errText = (e: unknown): string => {
  if (e instanceof ApiError) return `${e.code}：${e.message}`;
  if (e instanceof Error) return e.message;
  return String(e);
};
