// 工作区注册表条目类型（单独文件避免 workspace.ts 与其它模块循环依赖）。
export interface ProjectRegistryEntry {
  projectId: string;
  title: string;
  createdAt: string;
  updatedAt: string;
  registeredAt: string;
}
