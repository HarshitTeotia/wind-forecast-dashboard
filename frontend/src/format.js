const numbers = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1 });
const signedNumbers = new Intl.NumberFormat('en-GB', { maximumFractionDigits: 1, signDisplay: 'exceptZero' });
const clock = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const timestamp = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
const calendar = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/London', day: 'numeric', month: 'long', year: 'numeric' });

export const isNumber = (value) => typeof value === 'number' && Number.isFinite(value);
export const number = (value, signed = false) => isNumber(value) ? (signed ? signedNumbers : numbers).format(value) : 'N/A';
export const mw = (value, signed = false) => isNumber(value) ? `${number(value, signed)} MW` : 'N/A';
export const percent = (value) => isNumber(value) ? `${number(value * 100)}%` : 'N/A';
export const ukTime = (value) => value ? clock.format(new Date(value)) : 'N/A';
export const ukTimestamp = (value) => value ? `${timestamp.format(new Date(value))} UK` : 'N/A';
export const ukDate = (value) => calendar.format(new Date(`${value}T12:00:00Z`));
