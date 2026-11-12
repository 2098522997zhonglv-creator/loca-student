/** 替代 @arco-design/web-vue/es/table/interface，放宽 TableColumnData。 */
export type TableColumnData = Record<string, any>;
export type TableData = Record<string, any>;
export type TableBorder = boolean | string;
export type TableRowSize = string | number;
export type TablePagePosition = string;
export type TableSortable = Record<string, any>;
export type TableFilterable = Record<string, any>;
export type TableOperationColumn = Record<string, any>;
export type TableDraggable = Record<string, any>;
export type TableChangeExtra = Record<string, any>;
export type Sorter = any;
export type Filters = any;
export type TableDataWithRaw = Record<string, any>;
export type TableFilterData = Record<string, any>;
export const TABLE_PAGE_POSITION = ['tl', 'top', 'tr', 'bl', 'bottom', 'br'] as const;
