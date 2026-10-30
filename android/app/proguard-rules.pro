# 纯 Compose 应用，保留行号便于回溯
-keepattributes SourceFile,LineNumberTable
-renamesourcefileattribute SourceFile

# jaudiotagger 用反射加载标签读写器与 artwork 实现，必须整体保留
-keep class org.jaudiotagger.** { *; }
-dontwarn org.jaudiotagger.**
-dontwarn org.slf4j.**
