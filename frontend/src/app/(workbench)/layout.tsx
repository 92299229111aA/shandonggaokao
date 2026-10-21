import { WorkbenchShell } from "@/components/workbench/workbench-shell";

/**
 * 六页共用的工作台外壳。
 *
 * 放在路由分组里而不是根布局：URL 不变（`(workbench)` 不参与路径），
 * 但外壳只作用于这六页，将来加打印视图之类不需要导航的页面时不受牵连。
 */
export default function WorkbenchLayout({ children }: { children: React.ReactNode }) {
  return <WorkbenchShell>{children}</WorkbenchShell>;
}
