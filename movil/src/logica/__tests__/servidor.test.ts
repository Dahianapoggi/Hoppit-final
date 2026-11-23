import { servidorPropuesto } from '../servidor';

test('en la web propone el origen de la pagina', () => {
  expect(servidorPropuesto(true, 'http://localhost:8080')).toBe('http://localhost:8080');
  expect(servidorPropuesto(true, 'https://ejemplo.invalid')).toBe('https://ejemplo.invalid');
});

test('en Android no propone nada', () => {
  expect(servidorPropuesto(false, 'http://localhost:8080')).toBe('');
});

test('sin un origen http no propone nada', () => {
  expect(servidorPropuesto(true, undefined)).toBe('');
  expect(servidorPropuesto(true, 'file://')).toBe('');
  expect(servidorPropuesto(true, 'null')).toBe('');
});
