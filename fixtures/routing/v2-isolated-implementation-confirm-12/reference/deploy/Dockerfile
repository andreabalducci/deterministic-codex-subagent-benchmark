FROM mcr.microsoft.com/dotnet/aspnet:8.0
USER 65532
ENV ASPNETCORE_URLS=http://+:8080
COPY publish/ /app/
WORKDIR /app
ENTRYPOINT ["dotnet","Api.dll"]
