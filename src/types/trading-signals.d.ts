declare module 'trading-signals' {
  export class EMA {
    constructor(period: number);
    update(value: number): void;
    getResult(): number | null;
  }
}
