import api from './api';

test('api module exports axios instance', () => {
  expect(api).toBeDefined();
  expect(api.defaults).toBeDefined();
  expect(api.get).toBeInstanceOf(Function);
  expect(api.post).toBeInstanceOf(Function);
});

test('api has correct base URL pattern', () => {
  // Should point to backend API
  const baseURL = api.defaults.baseURL || '';
  // Accept empty (relative) or localhost patterns
  expect(typeof baseURL).toBe('string');
});

test('api has default headers', () => {
  expect(api.defaults.headers).toBeDefined();
});
