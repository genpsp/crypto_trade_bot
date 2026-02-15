declare module 'bs58' {
  export interface Bs58Converter {
    encode(buffer: Uint8Array | number[]): string;
    decode(value: string): Uint8Array;
    decodeUnsafe(value: string): Uint8Array | undefined;
  }

  const bs58: Bs58Converter;
  export default bs58;
}
