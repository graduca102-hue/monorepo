export const API_PROXY_HEADER_NAME = 'x-createcoin-proxy';
export const API_PROXY_HEADER_VALUE = '1';

export function getApiProxyHeaders(extra: Record<string, string> = {}): Record<string, string> {
  return {
    [API_PROXY_HEADER_NAME]: API_PROXY_HEADER_VALUE,
    ...extra,
  };
}
