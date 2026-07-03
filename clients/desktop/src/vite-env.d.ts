/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** "1" only when the JS↔Python scorer parity gate is green (WF-ADR-0042 §2). Set by the
   *  dev/build scripts; unset in an untrusted build → the local-mirror preview is withheld. */
  readonly VITE_PARITY_OK?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
