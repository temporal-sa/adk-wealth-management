import { render, screen, fireEvent } from '@testing-library/react';
import App from './App';

test('renders the chat with a Start Chat button', () => {
  render(<App />);
  expect(screen.getByRole('button', { name: 'Start Chat' })).toBeInTheDocument();
  expect(screen.getByPlaceholderText('Type a message...')).toBeDisabled();
});

test('the Chaos button opens and closes the chaos panel', () => {
  render(<App />);
  expect(screen.queryByText('Chaos panel')).not.toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Chaos' }));
  expect(screen.getByText('Chaos panel')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: 'Reset all' })).toBeInTheDocument();

  fireEvent.click(screen.getByRole('button', { name: 'Hide chaos' }));
  expect(screen.queryByText('Chaos panel')).not.toBeInTheDocument();
});
