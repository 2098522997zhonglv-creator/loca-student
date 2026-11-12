declare module '@arco-design/web-vue/es/tree/interface' {
  interface TreeNodeData {
    id?: string | number;
    key?: string | number;
    name?: string;
    title?: string;
    level?: number;
    [key: string]: any;
  }
}

export {};
