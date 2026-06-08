import { render, screen } from '@testing-library/react';
import App from './App';

test('renders tracker dashboard', () => {
  render(<App />);
  const linkElement = screen.getByText(/tracked media pages/i);
  expect(linkElement).toBeInTheDocument();
});
